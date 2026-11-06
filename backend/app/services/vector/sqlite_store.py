# -*- coding: utf-8 -*-
"""
内置 SQLite 向量库实现（本地默认）。

- 向量以 JSON 文本存于业务库同一 SQLite 文件，零外部依赖、零额外服务。
- 检索为暴力余弦相似度（向量已归一化，点积即余弦），适合中小规模知识库。
- 与 pgvector 保持完全一致的对外接口，便于本地/云端无缝切换。
"""
import json
import logging
import math

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.services import embeddings

logger = logging.getLogger(__name__)

_TABLE = "vector_chunks"


class SqliteVectorStore:
    name = "sqlite-embedded"

    def __init__(self):
        self._ensure_table()

    def _ensure_table(self) -> None:
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""CREATE TABLE IF NOT EXISTS {_TABLE} (
                        id          TEXT PRIMARY KEY,
                        doc_id      TEXT NOT NULL,
                        chunk_index INTEGER NOT NULL DEFAULT 0,
                        page        INTEGER,
                        filename    TEXT,
                        text        TEXT,
                        char_start  INTEGER,
                        char_end    INTEGER,
                        meta        TEXT,
                        embedding   TEXT NOT NULL
                    )"""
                )
            )
            existing = {r[1] for r in conn.execute(text(f"PRAGMA table_info({_TABLE})")).fetchall()}
            for col in ("char_start", "char_end", "meta"):
                if col not in existing:
                    ddl = "INTEGER" if col != "meta" else "TEXT"
                    conn.execute(text(f"ALTER TABLE {_TABLE} ADD COLUMN {col} {ddl}"))
            conn.execute(
                text(f"CREATE INDEX IF NOT EXISTS {_TABLE}_doc_idx ON {_TABLE}(doc_id)")
            )

    def add_chunks(self, chunks: list[dict]) -> None:
        # 向量化用 embed_text（可带章节标题增强召回），原文 text 单独保存用于精准定位
        vectors = embeddings.embed_documents([c.get("embed_text") or c["text"] for c in chunks])
        if not vectors:
            return
        known = {"doc_id", "page", "chunk_index", "filename", "char_start", "char_end"}
        rows = [
            {
                "id": c["id"],
                "doc_id": c["metadata"]["doc_id"],
                "chunk_index": c["metadata"].get("chunk_index", 0),
                "page": c["metadata"].get("page") or 0,
                "filename": c["metadata"].get("filename", ""),
                "text": c["text"],
                "char_start": c["metadata"].get("char_start"),
                "char_end": c["metadata"].get("char_end"),
                "meta": json.dumps(
                    {k: v for k, v in c["metadata"].items() if k not in known}, ensure_ascii=False
                ),
                "embedding": json.dumps([round(v, 6) for v in vec]),
            }
            for c, vec in zip(chunks, vectors)
        ]
        sql = text(
            f"""INSERT OR REPLACE INTO {_TABLE}
                (id, doc_id, chunk_index, page, filename, text, char_start, char_end, meta, embedding)
                VALUES (:id, :doc_id, :chunk_index, :page, :filename, :text,
                        :char_start, :char_end, :meta, :embedding)"""
        )
        with engine.begin() as conn:
            for i in range(0, len(rows), 64):
                conn.execute(sql, rows[i : i + 64])
        logger.info("SQLite 向量库已写入 %d 个切片", len(rows))

    def delete_by_doc(self, doc_id: str) -> None:
        with engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {_TABLE} WHERE doc_id = :doc_id"), {"doc_id": doc_id})
        logger.info("SQLite 向量库已删除文档 %s 的切片", doc_id)

    def search(
        self,
        question: str,
        top_k: int | None = None,
        threshold: float | None = None,
        cfg: dict | None = None,
    ) -> list[dict]:
        query = embeddings.embed_query(question, cfg=cfg)
        q_norm = math.sqrt(sum(v * v for v in query)) or 1.0
        limit = settings.SCORE_THRESHOLD if threshold is None else threshold
        k = top_k or settings.TOP_K

        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT id, doc_id, chunk_index, page, filename, text, char_start, char_end, meta, embedding"
                    f" FROM {_TABLE}"
                )
            ).mappings().all()

        hits = []
        for row in rows:
            vec = json.loads(row["embedding"])
            v_norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            dot = sum(a * b for a, b in zip(query, vec)) / (q_norm * v_norm)
            similarity = round(dot, 4)
            if similarity >= limit:
                meta = json.loads(row["meta"]) if row["meta"] else {}
                hits.append(
                    {
                        "text": row["text"],
                        "metadata": {
                            **meta,
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
        return hits[:k]

    def all_chunks(self) -> list[dict]:
        """全量切片（供 BM25 关键词检索建索引）。"""
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""SELECT id, doc_id, chunk_index, page, filename, text, char_start, char_end, meta
                        FROM {_TABLE}"""
                )
            ).mappings().all()
        return [
            {
                "id": row["id"],
                "text": row["text"],
                "metadata": {
                    **(json.loads(row["meta"]) if row["meta"] else {}),
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

    def get_chunks(self, doc_id: str, limit: int = 100) -> list[dict]:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""SELECT id, doc_id, chunk_index, page, filename, text, char_start, char_end, meta
                        FROM {_TABLE} WHERE doc_id = :doc_id
                        ORDER BY chunk_index LIMIT :limit"""
                ),
                {"doc_id": doc_id, "limit": limit},
            ).mappings().all()
        return [
            {
                "id": row["id"],
                "text": row["text"],
                "metadata": {
                    **(json.loads(row["meta"]) if row["meta"] else {}),
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
        with engine.connect() as conn:
            return int(conn.execute(text(f"SELECT count(*) FROM {_TABLE}")).scalar() or 0)
