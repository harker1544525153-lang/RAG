# -*- coding: utf-8 -*-
"""
数据库连接层：SQLAlchemy 引擎 + 会话工厂 + 建表。

同一套 ORM 模型同时支持：
- SQLite（本地开发，文件落盘 backend/data/rag.db）
- PostgreSQL（云端，Neon / Supabase 免费层）
切换只需修改 DATABASE_URL，无需改业务代码。
"""
import logging
import os

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base

logger = logging.getLogger(__name__)

_connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

# Serverless 下每取一次连接都 pre_ping 会多一次数据库往返（与 Neon 跨区时约 100~300ms）；
# 改用 pool_recycle：连接空闲超过 2 分钟就丢弃重建，避开 Neon 的空闲断连。
_pool_kwargs = {"pool_pre_ping": True}
if os.getenv("VERCEL") and settings.DATABASE_URL.startswith("postgres"):
    _pool_kwargs = {"pool_pre_ping": False, "pool_recycle": 120}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args,
    future=True,
    **_pool_kwargs,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


if settings.DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragma(dbapi_conn, _record):  # pragma: no cover - 仅本地生效
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _ensure_columns(conn, table: str, columns: dict[str, str]) -> None:
    """轻量迁移：为已存在的表补充缺失列（SQLite / PostgreSQL 通用）。"""
    try:
        if settings.DATABASE_URL.startswith("sqlite"):
            existing = {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}
        else:
            existing = {
                r[0] for r in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = :t"
                    ),
                    {"t": table},
                ).fetchall()
            }
        for col, ddl in columns.items():
            if col in existing:
                continue
            if settings.DATABASE_URL.startswith("sqlite"):
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            else:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ddl}"))
    except Exception:
        logger.warning("%s 表补列失败", table, exc_info=True)


def _sqlite_rebuild_legacy_tables() -> None:
    """SQLite 结构收敛（仅本地库需要，PostgreSQL 用 DROP CONSTRAINT）。

    1) categories.name 去掉全局唯一约束：多账号各自维护同名分类（如「通用文档」）
    2) qa_categories 主键改为 (user_id, id)：问答归档分类按账号独立配置

    重建期间临时关闭外键检查，避免 documents 的外键引用导致 DROP 失败。
    """
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cats_sql = (
            cur.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='categories'"
            ).fetchone() or [""]
        )[0] or ""
        qa_sql = (
            cur.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='qa_categories'"
            ).fetchone() or [""]
        )[0] or ""
        need_cats = "UNIQUE" in cats_sql.upper()
        need_qa = "user_id" not in qa_sql
        if not need_cats and not need_qa:
            return

        cur.execute("PRAGMA foreign_keys=OFF")
        if need_cats:
            cur.execute(
                "CREATE TABLE categories__new ("
                " id VARCHAR(32) NOT NULL PRIMARY KEY,"
                " user_id VARCHAR(32) DEFAULT '',"
                " name VARCHAR(80) NOT NULL,"
                " description TEXT,"
                " color VARCHAR(16),"
                " strategy VARCHAR(16),"
                " chunk_size INTEGER,"
                " chunk_overlap INTEGER,"
                " created_at DATETIME)"
            )
            cur.execute(
                "INSERT INTO categories__new "
                "(id,user_id,name,description,color,strategy,chunk_size,chunk_overlap,created_at) "
                "SELECT id, COALESCE(user_id,''), name, description, color, strategy,"
                " chunk_size, chunk_overlap, created_at FROM categories"
            )
            cur.execute("DROP TABLE categories")
            cur.execute("ALTER TABLE categories__new RENAME TO categories")
            logger.info("结构迁移：categories 已移除 name 唯一约束")
        if need_qa:
            cur.execute(
                "CREATE TABLE qa_categories__new ("
                " user_id VARCHAR(32) NOT NULL DEFAULT '',"
                " id VARCHAR(16) NOT NULL,"
                " label VARCHAR(40) NOT NULL,"
                " color VARCHAR(16),"
                " keywords TEXT,"
                " sort_order INTEGER,"
                " enabled INTEGER,"
                " PRIMARY KEY (user_id, id))"
            )
            cur.execute(
                "INSERT INTO qa_categories__new "
                "(user_id,id,label,color,keywords,sort_order,enabled) "
                "SELECT '', id, label, color, keywords, sort_order, enabled FROM qa_categories"
            )
            cur.execute("DROP TABLE qa_categories")
            cur.execute("ALTER TABLE qa_categories__new RENAME TO qa_categories")
            logger.info("结构迁移：qa_categories 已改为按账号复合主键")
        raw.commit()
        cur.execute("PRAGMA foreign_keys=ON")
    finally:
        raw.close()


def _pg_relax_category_unique() -> None:
    """PostgreSQL：删除 categories.name 的唯一约束（按账号隔离后允许同名）。"""
    with engine.begin() as conn:
        names = [
            r[0] for r in conn.execute(
                text(
                    "SELECT constraint_name FROM information_schema.table_constraints "
                    "WHERE table_name = 'categories' AND constraint_type = 'UNIQUE'"
                )
            ).fetchall()
        ]
        for name in names:
            conn.execute(text(f'ALTER TABLE categories DROP CONSTRAINT IF EXISTS "{name}"'))


def _pg_rebuild_qa_categories() -> None:
    """PostgreSQL：qa_categories 旧版主键是单列 id，多账号后必须改为 (user_id, id)。

    `create_all` 不会修改已存在的表：旧表既没有 user_id 列，插入第二个账号的同名分类
    还会主键冲突。云端是已上线的 Neon 库，因此这里做一次幂等重建。
    """
    try:
        with engine.begin() as conn:
            cols = {
                r[0] for r in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'qa_categories'"
                    )
                ).fetchall()
            }
            if not cols:
                return
            conn.execute(
                text(
                    "ALTER TABLE qa_categories "
                    "ADD COLUMN IF NOT EXISTS user_id VARCHAR(32) DEFAULT ''"
                )
            )
            pk = [
                r[0] for r in conn.execute(
                    text(
                        "SELECT kcu.column_name FROM information_schema.table_constraints tc "
                        "JOIN information_schema.key_column_usage kcu "
                        " ON tc.constraint_name = kcu.constraint_name "
                        " AND tc.table_name = kcu.table_name "
                        "WHERE tc.table_name = 'qa_categories' "
                        " AND tc.constraint_type = 'PRIMARY KEY' "
                        "ORDER BY kcu.ordinal_position"
                    )
                ).fetchall()
            ]
            if pk == ["user_id", "id"]:
                return
            names = [
                r[0] for r in conn.execute(
                    text(
                        "SELECT constraint_name FROM information_schema.table_constraints "
                        "WHERE table_name = 'qa_categories' AND constraint_type = 'PRIMARY KEY'"
                    )
                ).fetchall()
            ]
            for name in names:
                conn.execute(text(f'ALTER TABLE qa_categories DROP CONSTRAINT IF EXISTS "{name}"'))
            conn.execute(text("ALTER TABLE qa_categories ADD PRIMARY KEY (user_id, id)"))
            logger.info("结构迁移：qa_categories 已改为按账号复合主键（PostgreSQL）")
    except Exception:
        logger.warning("qa_categories 结构迁移失败（PostgreSQL）", exc_info=True)


def _ensure_user_indexes() -> None:
    """为存量表补齐 user_id 索引（create_all 不会修改已存在的表）。"""
    with engine.begin() as conn:
        for table in ("categories", "documents", "chat_sessions"):
            try:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_user_id ON {table}(user_id)"))
            except Exception:
                logger.warning("%s 表 user_id 索引创建失败", table, exc_info=True)


def init_db() -> None:
    """建表 + 兼容旧版本的轻量 ALTER。"""
    if settings.is_postgres:
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception:  # 扩展已存在或无权限时忽略，交给 pgvector 后端自行报错
            logger.warning("pgvector 扩展启用失败（可能已存在或无权限）", exc_info=True)
    Base.metadata.create_all(engine)

    # 轻量迁移：给 ChatSession 补 source / visitor_name / category 列（旧版数据库没有）
    with engine.begin() as conn:
        existing = {
            r[1] for r in conn.execute(text("PRAGMA table_info(chat_sessions)")).fetchall()
        } if settings.DATABASE_URL.startswith("sqlite") else set()
        # Postgres 场景：列存在会报 duplicate，用 IF NOT EXISTS
        for col, default in [
            ("source", "'admin'"),
            ("visitor_name", "''"),
            ("category", "''"),
        ]:
            if col not in existing:
                try:
                    if settings.DATABASE_URL.startswith("sqlite"):
                        conn.execute(text(
                            f"ALTER TABLE chat_sessions ADD COLUMN {col} VARCHAR(80) DEFAULT {default}"
                        ))
                    else:
                        conn.execute(text(
                            f"ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS {col} VARCHAR(80) DEFAULT {default}"
                        ))
                except Exception:
                    pass  # 列已存在则忽略

    with engine.begin() as conn:
        # 原文 HTML（云端原文件丢失时仍可预览原文）
        _ensure_columns(conn, "documents", {"preview_html": "TEXT"})
        # 分类分块策略（按分类采用不同分块方式）
        _ensure_columns(
            conn,
            "categories",
            {
                "strategy": "VARCHAR(16) DEFAULT 'standard'",
                "chunk_size": "INTEGER DEFAULT 768",
                "chunk_overlap": "INTEGER DEFAULT 96",
            },
        )
        # 文档结构化元数据：分级 / 权重 / 标签 / 有效期（检索过滤与加权）
        _ensure_columns(
            conn,
            "documents",
            {
                "tier": "VARCHAR(16) DEFAULT 'standard'",
                "importance": "INTEGER DEFAULT 5",
                "doc_type": "VARCHAR(32) DEFAULT ''",
                "module": "VARCHAR(80) DEFAULT ''",
                "scenario": "VARCHAR(80) DEFAULT ''",
                "visibility": "VARCHAR(16) DEFAULT 'public'",
                "effective_date": "VARCHAR(10)",
                "expire_date": "VARCHAR(10)",
                "auto_category": "INTEGER DEFAULT 0",
            },
        )
        # 多账号隔离：分类 / 文档 / 会话归属用户
        _ensure_columns(
            conn,
            "categories",
            {"user_id": "VARCHAR(32) DEFAULT ''"},
        )
        _ensure_columns(
            conn,
            "documents",
            {"user_id": "VARCHAR(32) DEFAULT ''"},
        )
        _ensure_columns(
            conn,
            "chat_sessions",
            {"user_id": "VARCHAR(32) DEFAULT ''"},
        )

    # 结构收敛：categories 允许各账号同名分类；qa_categories 按账号复合主键
    if settings.DATABASE_URL.startswith("sqlite"):
        _sqlite_rebuild_legacy_tables()
    else:
        _pg_relax_category_unique()
        _pg_rebuild_qa_categories()
    _ensure_user_indexes()

    logger.info("数据库初始化完成: %s", "postgresql" if settings.is_postgres else "sqlite")


def get_db():
    """FastAPI 依赖：每个请求独立会话，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
