# -*- coding: utf-8 -*-
"""向量库统一接口：ChromaDB（本地）与 pgvector（云端）实现同一套方法。"""
from typing import Protocol


class VectorStore(Protocol):
    name: str

    def add_chunks(self, chunks: list[dict]) -> None: ...

    def delete_by_doc(self, doc_id: str) -> None: ...

    def search(
        self, question: str, top_k: int | None = None, threshold: float | None = None
    ) -> list[dict]: ...

    def get_chunks(self, doc_id: str, limit: int = 100) -> list[dict]: ...

    def count(self) -> int: ...
