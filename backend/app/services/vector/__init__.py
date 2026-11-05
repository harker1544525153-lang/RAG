# -*- coding: utf-8 -*-
"""向量库工厂：按 VECTOR_BACKEND（或 DATABASE_URL）自动选择实现。"""
import logging
from typing import Optional

from app.config import settings
from app.services.vector.base import VectorStore

logger = logging.getLogger(__name__)

_store: Optional[VectorStore] = None
_store_key: Optional[str] = None


def get_vector_store() -> VectorStore:
    global _store, _store_key
    backend = settings.effective_vector_backend
    if _store is None or _store_key != backend:
        if backend == "pgvector":
            from app.services.vector.pgvector_store import PgVectorStore

            _store = PgVectorStore()
        elif backend == "chroma":
            from app.services.vector.chroma_store import ChromaVectorStore

            _store = ChromaVectorStore()
        else:
            from app.services.vector.sqlite_store import SqliteVectorStore

            _store = SqliteVectorStore()
        _store_key = backend
        logger.info("向量库后端: %s", backend)
    return _store
