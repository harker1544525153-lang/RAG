# -*- coding: utf-8 -*-
"""
pgvector 向量库实现（PostgreSQL 云端模式默认）。

- 复用同一个 Postgres 数据库，切片表与业务表同库，迁移/备份只需一个连接串。
- 使用 HNSW + 余弦距离索引，检索距离换算 similarity = 1 - distance。
- 表在首次写入时按实际向量维度创建，避免 provider 切换时维度不一致。
"""
import logging
import re

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.services import embeddings

logger = logging.getLogger(__name__)

_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _vec_literal(vector: list[float]) -> str:
    """把 Python 浮点数组转成 pgvector 可 CAST 的 '[a,b,c]' 字面量。"""
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"


class PgVectorStore:
    name = "pgvector"

    def __init__(self):
        if not settings.is_postgres:
            raise RuntimeError("pgvector 后端需要 PostgreSQL DATABASE_URL")
        table = settings.VECTOR_COLLECTION
        if not _TABLE_RE.match(table):
            raise ValueError(f"非法向量表名: {table}")
        self.table = table
        self._ready_dim: int | None = None
        self._table_exists: bool | None = None

    # ---------- 内部 ----------

    def _exists(self) -> bool:
        # 缓存查询结果：每次问答都查 to_regclass 会多一次数据库往返（跨区约 100~300ms）
        if self._table_exists is not None:
            return self._table_exists
        with engine.connect() as conn:
            reg = conn.execute(text("SELECT to_regclass(:name)"), {"name": f"public.{self.table}"}).scalar()
        self._table_exists = bool(reg)
        return self._table_exists

    def _ensure_table(self, dim: int) -> None:
        if self._ready_dim == dim:
            return
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""CREATE TABLE IF NOT EXISTS {self.table} (
                        id          TEXT PRIMARY KEY,
                        doc_id      TEXT NOT NULL,
                        chunk_index INTEGER NOT NULL DEFAULT 0,
                        page        INTEGER,
                        filename    TEXT,
                        text        TEXT,
                        char_start  INTEGER,
                        char_end    INTEGER,
                        embedding   vector({dim}) NOT NULL
                    )"""
                )
            )
            # 兼容旧表：切片字符偏移用于前端精准锚定原文片段
            for col in ("char_start", "char_end"):
                conn.execute(
                    text(f"ALTER TABLE {self.table} ADD COLUMN IF NOT EXISTS {col} INTEGER")
                )
            conn.execute(
                text(f"CREATE INDEX IF NOT EXISTS {self.table}_doc_idx ON {self.table}(doc_id)")
            )
        self._ready_dim = dim
        self._table_exists = True
        # HNSW 索引对 >2000 维向量不支持；失败时降级为顺序扫描（小规模知识库影响有限）
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS {self.table}_emb_idx "
                        f"ON {self.table} USING hnsw (embedding vector_cosine_ops)"
                    )
                )
        except Exception:
            logger.warning(
                "pgvector HNSW 索引创建失败（维度 %d 可能超过限制），已降级为顺序扫描", dim, exc_info=True
            )
        logger.info("pgvector 表 %s 就绪 (dim=%d)", self.table, dim)

    # ---------- 对外 ----------

    def add_chunks(self, chunks: list[dict]) -> None:
        vectors = embeddings.embed_documents([c["text"] for c in chunks])
        if not vectors:
            return
        self._ensure_table(len(vectors[0]))
        rows = [
            {
                "id": c["id"],
                "doc_id": c["metadata"]["doc_id"],
                "chunk_index": c["metadata"].get("chunk_index", 0),
                "page": c["metadata"].get("page") or None,
                "filename": c["metadata"].get("filename", ""),
                "text": c["text"],
                "char_start": c["metadata"].get("char_start"),
                "char_end": c["metadata"].get("char_end"),
                "embedding": _vec_literal(v),
            }
            for c, v in zip(chunks, vectors)
        ]
        sql = text(
            f"""INSERT INTO {self.table}
                (id, doc_id, chunk_index, page, filename, text, char_start, char_end, embedding)
                VALUES (:id, :doc_id, :chunk_index, :page, :filename, :text,
                        :char_start, :char_end, CAST(:embedding AS vector))"""
        )
        with engine.begin() as conn:
            for i in range(0, len(rows), 64):
                conn.execute(sql, rows[i : i + 64])
        logger.info("pgvector 已写入 %d 个切片", len(rows))

    def delete_by_doc(self, doc_id: str) -> None:
        if not self._exists():
            return
        with engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {self.table} WHERE doc_id = :doc_id"), {"doc_id": doc_id})
        logger.info("pgvector 已删除文档 %s 的切片", doc_id)

    def search(
        self, question: str, top_k: int | None = None, threshold: float | None = None
    ) -> list[dict]:
        vector = embeddings.embed_query(question)
        # 检索只读：表由入库流程创建，此处不再重复建表（每次冷启动省 2~3 次 DDL 往返）
        if not self._exists():
            return []
        limit = settings.SCORE_THRESHOLD if threshold is None else threshold
        k = top_k or settings.TOP_K
        q_literal = _vec_literal(vector)
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""SELECT text, doc_id, chunk_index, page, filename,
                               char_start, char_end,
                               embedding <=> CAST(:q AS vector) AS distance
                        FROM {self.table}
                        ORDER BY embedding <=> CAST(:q AS vector)
                        LIMIT :k"""
                ),
                {"q": q_literal, "k": k},
            ).mappings().all()
        hits = []
        for row in rows:
            similarity = round(1.0 - float(row["distance"]), 4)
            if similarity >= limit:
                hits.append(
                    {
                        "text": row["text"],
                        "metadata": {
                            "doc_id": row["doc_id"],
                            "filename": row["filename"] or "",
                            "page": row["page"] or 0,
                            "chunk_index": row["chunk_index"],
                            "char_start": row["char_start"],
                            "char_end": row["char_end"],
                        },
                        "similarity": similarity,
                    }
                )
        hits.sort(key=lambda h: h["similarity"], reverse=True)
        logger.info("pgvector 检索 '%s': 命中 %d 条", question[:30], len(hits))
        return hits

    def get_chunks(self, doc_id: str, limit: int = 100) -> list[dict]:
        if not self._exists():
            return []
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""SELECT id, text, doc_id, chunk_index, page, filename, char_start, char_end
                        FROM {self.table} WHERE doc_id = :doc_id
                        ORDER BY chunk_index LIMIT :limit"""
                ),
                {"doc_id": doc_id, "limit": limit},
            ).mappings().all()
        return [
            {
                "id": row["id"],
                "text": row["text"],
                "metadata": {
                    "doc_id": row["doc_id"],
                    "filename": row["filename"] or "",
                    "page": row["page"] or 0,
                    "chunk_index": row["chunk_index"],
                    "char_start": row["char_start"],
                    "char_end": row["char_end"],
                },
            }
            for row in rows
        ]

    def count(self) -> int:
        if not self._exists():
            return 0
        with engine.connect() as conn:
            return int(conn.execute(text(f"SELECT count(*) FROM {self.table}")).scalar() or 0)
