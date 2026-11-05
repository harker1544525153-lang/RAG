# -*- coding: utf-8 -*-
"""
向量化服务（双模式，可通过 EMBEDDING_PROVIDER 切换）：

- local  本地开源 BGE（sentence-transformers），离线可用，首次自动下载模型
- openai OpenAI 兼容 Embedding API（SiliconFlow / 智谱 / OpenAI 等），适合云端轻量部署
- hash   确定性哈希向量（无外部依赖），仅用于离线联调 / CI 冒烟测试

统一对外接口：embed_documents(texts) / embed_query(text) / dim
"""
import hashlib
import logging
import math
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional, Protocol

from app.config import settings

logger = logging.getLogger(__name__)


def _hf_cache_exists(model_name: str) -> bool:
    """判断模型是否已完整落在本地 HF 缓存中（存在 snapshots 目录且非空）。

    用于离线加载：缓存命中时传 local_files_only=True，避免再去 huggingface.co
    做一次元数据校验（国内网络会超时重试，使首次提问卡住数十秒）。
    """
    hub = Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface")) / "hub"
    snapshots = hub / ("models--" + model_name.replace("/", "--")) / "snapshots"
    return snapshots.is_dir() and any(snapshots.iterdir())

# BGE 中文模型查询侧指令前缀（官方推荐，可显著提升检索效果）
_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

# 常见模型维度映射（用于 pgvector 建表前预估）
_KNOWN_DIMS = {
    "BAAI/bge-large-zh-v1.5": 1024,
    "BAAI/bge-base-zh-v1.5": 768,
    "BAAI/bge-small-zh-v1.5": 512,
    "BAAI/bge-m3": 1024,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class LocalBGEEmbedder:
    """本地 sentence-transformers 模型（默认 BGE 系列）。"""

    def __init__(self, model_name: str, device: str, batch_size: int = 32):
        from sentence_transformers import SentenceTransformer

        local_only = _hf_cache_exists(model_name)
        logger.info(
            "加载本地向量化模型 %s (device=%s, 离线=%s) ...",
            model_name, device, local_only,
        )
        self.model = SentenceTransformer(
            model_name, device=device, local_files_only=local_only
        )
        self.batch_size = batch_size
        self.name = f"local:{model_name}"
        self.dim = (
            self.model.get_sentence_embedding_dimension()
            or _KNOWN_DIMS.get(model_name, 1024)
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=len(texts) > 100,
        )
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        vector = self.model.encode(
            [_QUERY_INSTRUCTION + text], normalize_embeddings=True
        )[0]
        return vector.tolist()


class OpenAICompatEmbedder:
    """OpenAI 兼容 Embedding API（云端推荐，无需本地模型）。"""

    def __init__(self, api_key: str, base_url: str, model: str, batch_size: int = 32):
        if not api_key:
            raise RuntimeError(
                "未配置向量化 API Key，请在管理端「设置 → 模型服务」填写，或复用大模型 Key"
            )
        if not model:
            raise RuntimeError(
                "openai 向量化模式需要模型名（如 text-embedding-v4 / qwen3.7-text-embedding-flash）"
            )
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url or None)
        self.api_model = model
        self.batch_size = batch_size
        self.name = f"openai:{model}"
        self.dim = settings.EMBEDDING_DIM or _KNOWN_DIMS.get(model, 0)

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        batch = self.batch_size
        for i in range(0, len(texts), batch):
            part = texts[i : i + batch]
            resp = self.client.embeddings.create(model=self.api_model, input=part)
            vectors.extend([item.embedding for item in resp.data])
        if not self.dim and vectors:
            self.dim = len(vectors[0])
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._encode([text])[0]


class HashEmbedder:
    """确定性哈希向量：单字 + 双字片段哈希到固定维度，仅用于流程验证。"""

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.name = f"hash:{dim}"

    def _encode(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        clean = "".join(text.lower().split())
        if not clean:
            return vec
        grams = list(clean) + [clean[i : i + 2] for i in range(len(clean) - 1)]
        for gram in grams:
            h = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._encode(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._encode(text)


_embedder: Optional[Embedder] = None
_embedder_key: Optional[str] = None
_embedder_lock = threading.Lock()

# 问题向量缓存：云端 Embedding 每次都要一次网络请求，重复问题直接复用可省 100ms+
_QUERY_CACHE_SIZE = 256
_query_cache: "OrderedDict[str, list[float]]" = OrderedDict()
_query_cache_lock = threading.Lock()


def _cfg() -> dict:
    """运行时配置（设置页可改，保存后立即生效）。"""
    from app.services import settings_service

    return settings_service.get_runtime()


def _openai_params(cfg: dict) -> tuple[str, str, str]:
    """openai 模式的连接参数。

    优先使用向量化专用 Key；未单独配置时复用大模型 Key（同为 OpenAI 兼容协议），
    这样切换在线向量化只需选择 provider 与模型。
    """
    api_key = (cfg.get("embedding_api_key") or "").strip()
    base_url = (cfg.get("embedding_base_url") or "").strip()
    model = (cfg.get("embedding_api_model") or "").strip()
    if api_key:
        return api_key, base_url, model
    llm_key = (cfg.get("llm_api_key") or "").strip()
    if llm_key:
        return llm_key, base_url or (cfg.get("llm_base_url") or ""), model or "text-embedding-v4"
    raise RuntimeError(
        "openai 向量化模式需要 API Key：请在管理端「设置 → 模型服务」填写向量化 Key，或复用大模型 Key"
    )


def _build_embedder(cfg: dict) -> Embedder:
    provider = (cfg.get("embedding_provider") or "local").lower()
    batch = int(cfg.get("embedding_batch_size") or 32)
    if provider == "local":
        return LocalBGEEmbedder(cfg.get("embedding_local_model") or settings.EMBEDDING_MODEL, settings.EMBEDDING_DEVICE, batch)
    if provider == "openai":
        api_key, base_url, model = _openai_params(cfg)
        return OpenAICompatEmbedder(api_key, base_url, model, batch)
    if provider == "hash":
        return HashEmbedder(settings.EMBEDDING_DIM or 256)
    raise ValueError(f"不支持的向量化方式: {provider}（可选 local / openai / hash）")


def get_embedder() -> Embedder:
    """懒加载单例；运行时配置变化时自动重建。加锁避免并发请求重复加载模型。"""
    global _embedder, _embedder_key
    cfg = _cfg()
    key = "|".join(
        str(cfg.get(k))
        for k in (
            "embedding_provider",
            "embedding_local_model",
            "embedding_api_model",
            "embedding_base_url",
            "embedding_api_key",
            "embedding_batch_size",
        )
    ) + f"|{cfg.get('llm_api_key')}"
    with _embedder_lock:
        if _embedder is None or _embedder_key != key:
            _embedder = _build_embedder(cfg)
            _embedder_key = key
            logger.info("向量化服务就绪: %s (dim=%s)", _embedder.name, _embedder.dim or "auto")
        return _embedder


def reset() -> None:
    """配置变更（如切换向量模型）后丢弃已加载实例与问题缓存，下次调用按新配置重建。"""
    global _embedder, _embedder_key
    with _embedder_lock:
        _embedder = None
        _embedder_key = None
    with _query_cache_lock:
        _query_cache.clear()


def warmup() -> None:
    """启动预热：把冷启动的模型加载时间从"用户首次提问"挪到"服务启动"。"""
    try:
        get_embedder()
    except Exception:
        logger.exception("向量化模型预热失败，将在首次使用时重试")



def embed_documents(texts: list[str]) -> list[list[float]]:
    return get_embedder().embed_documents(texts)


def embed_query(text: str) -> list[float]:
    """问题向量化（带实例内 LRU 缓存，重复提问不再走网络/模型）。"""
    key = text.strip()
    with _query_cache_lock:
        cached = _query_cache.get(key)
        if cached is not None:
            _query_cache.move_to_end(key)
            return cached
    vector = get_embedder().embed_query(key)
    with _query_cache_lock:
        _query_cache[key] = vector
        if len(_query_cache) > _QUERY_CACHE_SIZE:
            _query_cache.popitem(last=False)
    return vector


def dim() -> int:
    return get_embedder().dim
