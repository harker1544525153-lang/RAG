# -*- coding: utf-8 -*-
"""
RAG 编排层：检索 -> 构建上下文 -> 交由 DeepSeek 流式生成。

提问时：问题向量化 -> 向量库召回 TopK -> 拼 Prompt -> LLM 流式回答
"""
import logging
from datetime import date, datetime
from typing import Generator

from app.config import settings
from app.services import bm25, settings_service, web_search
from app.services.span import locate_span
from app.services.vector import get_vector_store

logger = logging.getLogger(__name__)

TIER_LABELS = {"core": "核心权威", "standard": "普通参考", "archived": "过期归档"}


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _rank_hits(
    hits: list[dict], filters: dict, runtime: dict, terms: list[str] | None = None
) -> list[dict]:
    """
    数据分级 + 时间权重 + 关键词命中 + 元数据过滤（在召回后统一加权重排）。

    final = 相关性 × 分级权重 × 时间衰减 × 重要度系数
    - 相关性：向量余弦相似度；仅有 BM25 命中的切片用归一化 RRF 分
    - 关键词强命中（英文术语/编号/引号短语）：把相关性抬到 0.78 起，命中越多越高
    - 分级权重：核心权威 > 普通参考 > 过期归档
    - 时间权重：越新越优先，按半衰期指数衰减，并设下限避免旧但有效资料被完全压制
    - 权限/模块/场景：请求带过滤条件时只保留匹配切片（空标签视为「通用」，仅在未指定过滤时保留）
    """
    today = date.today()
    tier_weights = {
        "core": float(runtime.get("tier_core_weight", 1.15)),
        "standard": float(runtime.get("tier_standard_weight", 1.0)),
        "archived": float(runtime.get("tier_archived_weight", 0.7)),
    }
    half_life = max(30.0, float(runtime.get("recency_half_life_days", 365) or 365))
    floor = max(0.5, float(runtime.get("recency_floor", 0.75) or 0.75))
    filter_expired = bool(runtime.get("filter_expired", True))
    visibility = (filters or {}).get("visibility") or "all"
    module = (filters or {}).get("module") or ""
    scenario = (filters or {}).get("scenario") or ""

    ranked = []
    for hit in hits:
        meta = hit.get("metadata") or {}
        tier = meta.get("tier") or "standard"

        if visibility != "all" and (meta.get("visibility") or "public") != visibility:
            continue
        if module and (meta.get("module") or "") != module:
            continue
        if scenario and (meta.get("scenario") or "") != scenario:
            continue

        expire = _parse_date(meta.get("expire_date"))
        expired = bool(expire and expire < today and tier != "core")
        if expired and filter_expired:
            continue

        tier_w = tier_weights.get(tier, 1.0)
        if expired:  # 未开启过滤时过期文档仍显著降权
            tier_w *= 0.6

        effective = _parse_date(meta.get("effective_date"))
        if effective:
            age_days = max(0, (today - effective).days)
            recency_w = max(floor, 0.5 ** (age_days / half_life))
        else:
            recency_w = 1.0

        importance = int(meta.get("importance") or 5)
        imp_w = 0.5 + max(1, min(10, importance)) * 0.1

        # 相关性基础：优先用余弦相似度；仅 BM25 召回的切片回退到 RRF 融合分
        relevance = hit.get("similarity")
        if relevance is None:
            relevance = hit.get("rrf") or 0.0

        # 关键词强命中：英文术语/编号/引号短语出现在切片中时明显抬高相关性
        matched = bm25.keyword_match(hit.get("text") or "", terms) if terms else []
        if matched:
            keyword_floor = min(0.98, 0.78 + 0.06 * (len(matched) - 1))
            relevance = max(relevance, keyword_floor)

        hit["score"] = round(relevance * tier_w * recency_w * imp_w, 4)
        hit["keyword_hits"] = matched
        hit["weights"] = {
            "tier": round(tier_w, 3),
            "recency": round(recency_w, 3),
            "importance": round(imp_w, 3),
        }
        ranked.append(hit)

    ranked.sort(key=lambda h: h["score"], reverse=True)
    return ranked


def _hit_key(hit: dict) -> tuple[str, int]:
    """切片唯一标识：同一文档内 chunk_index 唯一。"""
    meta = hit.get("metadata") or {}
    return (str(meta.get("doc_id") or ""), int(meta.get("chunk_index") or 0))


def _fuse_rrf(vector_hits: list[dict], keyword_hits: list[dict], k: int = 60) -> list[dict]:
    """RRF 融合向量召回与 BM25 召回，返回带归一化 rrf 分的候选集。

    向量召回覆盖语义相近但字面不同的切片，BM25 覆盖关键词/编号的精确命中；
    RRF 只看名次、不混用量纲，因此不必把余弦相似度和 BM25 原始分强行对齐。
    """
    merged: dict[tuple, dict] = {}
    rrf: dict[tuple, float] = {}
    for rank, hit in enumerate(vector_hits):
        key = _hit_key(hit)
        rrf[key] = rrf.get(key, 0.0) + 1.0 / (k + rank + 1)
        merged.setdefault(key, dict(hit))
    for rank, hit in enumerate(keyword_hits):
        key = _hit_key(hit)
        rrf[key] = rrf.get(key, 0.0) + 1.0 / (k + rank + 1)
        item = merged.setdefault(key, dict(hit))
        item["bm25"] = hit.get("bm25")

    top = max(rrf.values()) or 1.0
    results = []
    for key, hit in merged.items():
        item = dict(hit)
        item["rrf"] = round(rrf[key] / top, 4)
        results.append(item)
    return results


def build_context(hits: list[dict], question: str = "") -> tuple[str, list[dict]]:
    """
    将召回切片格式化为带编号的参考资料文本。

    编号即引用编号：检索顺序 = 上下文顺序 = LLM 看到的 [n] = 前端卡片顺序，
    避免多处排序导致引用错位。
    :return: (context_text, sources) sources 供前端展示引用卡片与精准定位
    """
    parts = []
    sources = []
    for i, hit in enumerate(hits, start=1):
        meta = hit["metadata"]
        text = hit["text"]
        page = meta.get("page")
        page_str = f"第 {page} 页" if page else ""
        # 章节信息并入上下文，帮助模型理解切片在原文中的位置
        section = meta.get("section") or ""
        section_str = f"｜章节：{section}" if section else ""
        parts.append(f"[{i}] （{meta['filename']} {page_str}{section_str}）\n{text}")
        # 细粒度片段：只高亮真正支撑回答的原文句子，而不是整段切片
        span_start, span_len = locate_span(question, text)
        span_text = text[span_start : span_start + span_len] if span_len else ""
        tier = meta.get("tier") or "standard"
        sources.append(
            {
                "index": i,
                "doc_id": meta.get("doc_id"),
                "filename": meta["filename"],
                "page": page or None,
                "chunk_index": meta.get("chunk_index", 0),
                "similarity": hit["similarity"],
                "score": hit.get("score", hit["similarity"]),
                "snippet": (span_text or text)[:120],
                "span_start": span_start,
                "span_len": span_len,
                "span_text": span_text,
                "chunk_char_start": meta.get("char_start"),
                "chunk_char_end": meta.get("char_end"),
                # 结构化元数据：前端来源卡片展示类型/分级/章节
                "doc_type": meta.get("doc_type") or meta.get("category_name") or "",
                "category_name": meta.get("category_name") or "",
                "tier": tier,
                "tier_label": TIER_LABELS.get(tier, "普通参考"),
                "section": section,
                "module": meta.get("module") or "",
                "scenario": meta.get("scenario") or "",
                "effective_date": meta.get("effective_date") or "",
                "strategy": meta.get("strategy") or "",
                "is_parent": bool(meta.get("is_parent")),
            }
        )
    return "\n\n".join(parts), sources


def append_web_results(
    question: str,
    context: str,
    sources: list[dict],
    max_results: int | None = None,
    runtime: dict | None = None,
) -> tuple[str, list[dict], str | None]:
    """联网时把 Tavily 结果追加为额外上下文（来源标记为 type=web）。

    返回 (新上下文, 来源, 失败原因)；失败原因用于在回答中提示用户。
    """
    hits, error = web_search.search(question, max_results=max_results, cfg=runtime)
    if not hits:
        return context, sources, error

    parts = []
    base = len(sources)
    for i, hit in enumerate(hits, start=base + 1):
        title = hit["title"]
        url = hit["url"]
        content = hit["content"][:800]
        parts.append(f"[{i}] （联网搜索：{title} | {url}）\n{content}")
        sources.append(
            {
                "index": i,
                "doc_id": None,
                "filename": title,
                "url": url,
                "page": None,
                "similarity": None,
                "type": "web",
                "snippet": content[:120],
            }
        )
    web_text = "【联网搜索结果】\n" + "\n\n".join(parts)
    merged = f"{context}\n\n{web_text}" if context else web_text
    return merged, sources, None


def retrieve_hits(
    question: str,
    top_k: int | None = None,
    threshold: float | None = None,
    filters: dict | None = None,
    doc_ids: set[str] | None = None,
    runtime: dict | None = None,
) -> tuple[list[dict], int]:
    """混合检索（向量 + BM25 RRF 融合）+ 分级/时间/关键词加权 + 元数据过滤。
    doc_ids 非 None 时只在该账号的文档范围内检索（多账号数据隔离）。
    runtime 显式传入账号配置：SSE 流在线程池中迭代时取不到请求的 contextvar。
    返回 (重排后的切片, 原始召回数量)。"""
    k = top_k or settings.TOP_K
    runtime = runtime or settings_service.get_runtime()
    store = get_vector_store()
    # 先多取候选，再做权限/模块/场景过滤与分级重排，避免过滤后数量不足
    fetch_k = max(k * 4, k + 8)
    raw = store.search(question, top_k=fetch_k, threshold=threshold, cfg=runtime)

    hybrid = bool(runtime.get("hybrid_search_enabled", True))
    terms: list[str] = []
    if hybrid:
        # 关键词强命中：英文术语、编号型号、引号内短语（中文由 BM25 分词排序体现）
        terms = bm25.extract_terms(question)
        try:
            keyword = bm25.search(question, store, fetch_k)
        except Exception:
            logger.warning("BM25 关键词检索失败，回退纯向量检索", exc_info=True)
            keyword = []
        if keyword:
            raw = _fuse_rrf(raw, keyword)

    if doc_ids is not None:
        allowed = set(doc_ids)
        raw = [
            h for h in raw
            if str((h.get("metadata") or {}).get("doc_id") or "") in allowed
        ]
    if not raw:
        return [], 0
    ranked = _rank_hits(raw, filters or {}, runtime, terms)
    return ranked[:k], len(raw)


def retrieve(
    question: str,
    top_k: int | None = None,
    threshold: float | None = None,
    filters: dict | None = None,
    doc_ids: set[str] | None = None,
    runtime: dict | None = None,
) -> tuple[str, list[dict]]:
    """检索并构建上下文。无命中时返回空串。"""
    hits, _raw_count = retrieve_hits(
        question, top_k=top_k, threshold=threshold, filters=filters,
        doc_ids=doc_ids, runtime=runtime,
    )
    if not hits:
        return "", []
    return build_context(hits, question)


def stream_answer(
    question: str,
    *,
    top_k: int | None = None,
    threshold: float | None = None,
    temperature: float | None = None,
    web_search_enabled: bool = False,
    web_search_mode: str = "fallback",
    web_max_results: int | None = None,
    filters: dict | None = None,
    doc_ids: set[str] | None = None,
    runtime: dict | None = None,
) -> Generator[dict, None, None]:
    """
    完整问答流（供 SSE 使用）：
      先 yield {"type": "sources", ...} 告知引用来源，
      再逐段 yield {"type": "token", ...} 输出回答增量。
    doc_ids 用于限定当前账号可见的文档范围。

    检索阶段同样在异常保护内：向量化失败（如未配置 API Key）时也要向前端
    yield error 事件，否则生成器首迭代抛错会让连接直接断掉，前端表现为"无回复"。
    """
    try:
        runtime = runtime or settings_service.get_runtime()
        hits, raw_count = retrieve_hits(
            question, top_k=top_k, threshold=threshold, filters=filters,
            doc_ids=doc_ids, runtime=runtime,
        )
        context, sources = build_context(hits, question) if hits else ("", [])

        need_web = web_search_enabled and (web_search_mode == "always" or not context)
        web_error = None
        if need_web:
            context, sources, web_error = append_web_results(
                question, context, sources, web_max_results, runtime
            )

        yield {"type": "sources", "sources": sources}

        if not context:
            msg = "知识库中未查询到相关内容，请换一种问法，或补充相关文档后再试。"
            if raw_count and not hits:
                msg = "当前检索范围内没有匹配资料，可尝试放宽「检索范围」筛选或补充该场景的文档。"
            if web_error:
                msg += f"\n\n（联网检索未成功：{web_error}）"
            yield {"type": "token", "content": msg}
            yield {"type": "done"}
            return

        from app.services.llm import stream_chat  # 延迟导入，便于测试

        for token in stream_chat(question, context, temperature=temperature, cfg=runtime):
            yield {"type": "token", "content": token}
        if web_error:
            yield {
                "type": "token",
                "content": f"\n\n> 联网检索未成功：{web_error}，以上回答仅基于知识库内容。",
            }
        yield {"type": "done"}
    except RuntimeError as e:  # 未配置 API Key 等
        yield {"type": "error", "message": str(e)}
    except Exception as e:
        logger.exception("问答流处理失败")
        yield {"type": "error", "message": f"问答处理失败：{e}"}
