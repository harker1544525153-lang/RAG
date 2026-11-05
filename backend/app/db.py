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


def init_db() -> None:
    """建表 + 兼容旧版本的轻量 ALTER。"""
    if settings.is_postgres:
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception:  # 扩展已存在或无权限时忽略，交给 pgvector 后端自行报错
            logger.warning("pgvector 扩展启用失败（可能已存在或无权限）", exc_info=True)
    Base.metadata.create_all(engine)

    # 轻量迁移：给 ChatSession 补 source / visitor_name 列（旧版数据库没有）
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

    # 轻量迁移：documents 补 preview_html（原文 HTML，云端原文件丢失时仍可预览原文）
    try:
        with engine.begin() as conn:
            if settings.DATABASE_URL.startswith("sqlite"):
                cols = {r[1] for r in conn.execute(text("PRAGMA table_info(documents)")).fetchall()}
                if "preview_html" not in cols:
                    conn.execute(text("ALTER TABLE documents ADD COLUMN preview_html TEXT"))
            else:
                conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS preview_html TEXT"))
    except Exception:
        logger.warning("documents 表补 preview_html 列失败", exc_info=True)

    logger.info("数据库初始化完成: %s", "postgresql" if settings.is_postgres else "sqlite")


def get_db():
    """FastAPI 依赖：每个请求独立会话，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
