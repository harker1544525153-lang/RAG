# -*- coding: utf-8 -*-
"""
ChromaDB 向量库实现（本地 SQLite 模式默认）。

- PersistentClient 落盘到 data/chroma，重启不丢数据。
- 余弦空间（hnsw:space=cosine），distance 换算 similarity = 1 - distance。
"""
import logging

import chromadb

from app.config import settings
from app.services import embeddings

logger = logging.getLogger(__name__)


class ChromaVectorStore:
    name = "chromadb"

    def __init__(self):
        self._client = chromadb.PersistentClient(path=str(settings.CHROMA_DIR))
        self._collection = self._client.get_or_create_collection(
            name=settings.VECTOR_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )

    def add_chunks(self, chunks: list[dict]) -> None:
        batch = 64
        for i in range(0, len(chunks), batch):
            part = chunks[i : i + batch]
            self._collection.add(
                ids=[c["id"] for c in part],
                documents=[c["text"] for c in part],
                metadatas=[c["metadata"] for c in part],
                embeddings=embeddings.embed_documents(
                    [c.get("embed_text") or c["text"] for c in part]
                ),
            )
        logger.info("Chroma 已写入 %d 个切片", len(chunks))

    def delete_by_doc(self, doc_id: str) -> None:
        self._collection.delete(where={"doc_id": doc_id})
        logger.info("Chroma 已删除文档 %s 的切片", doc_id)

    def search(
        self,
        question: str,
        top_k: int | None = None,
        threshold: float | None = None,
        cfg: dict | None = None,
    ) -> list[dict]:
        total = self._collection.count()
        if not total:
            return []
        k = min(top_k or settings.TOP_K, total)
        result = self._collection.query(
            query_embeddings=[embeddings.embed_query(question, cfg=cfg)],
            n_results=k,
            include=["documents", "metadatas", "distances"],
        )
        limit = settings.SCORE_THRESHOLD if threshold is None else threshold
        hits = []
        for text, meta, dist in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0]
        ):
            similarity = round(1.0 - dist, 4)
            if similarity >= limit:
                hits.append({"text": text, "metadata": meta, "similarity": similarity})
        hits.sort(key=lambda h: h["similarity"], reverse=True)
        logger.info("Chroma 检索 '%s': 命中 %d 条", question[:30], len(hits))
        return hits

    def all_chunks(self) -> list[dict]:
        """全量切片（供 BM25 关键词检索建索引）。"""
        data = self._collection.get(include=["documents", "metadatas"])
        return [
            {"id": cid, "text": text, "metadata": meta}
            for cid, text, meta in zip(data["ids"], data["documents"], data["metadatas"])
        ]

    def get_chunks(self, doc_id: str, limit: int = 100) -> list[dict]:
        data = self._collection.get(
            where={"doc_id": doc_id}, include=["documents", "metadatas"], limit=limit
        )
        chunks = [
            {"id": cid, "text": text, "metadata": meta}
            for cid, text, meta in zip(data["ids"], data["documents"], data["metadatas"])
        ]
        chunks.sort(key=lambda c: c["metadata"].get("chunk_index", 0))
        return chunks

    def count(self) -> int:
        return self._collection.count()
