# -*- coding: utf-8 -*-
"""
RAG 编排层：检索 -> 构建上下文 -> 交由 DeepSeek 流式生成。

提问时：问题向量化 -> 向量库召回 TopK -> 拼 Prompt -> LLM 流式回答
"""
import logging
from typing import Generator

from app.config import settings
from app.services import web_search
from app.services.span import locate_span
from app.services.vector import get_vector_store

logger = logging.getLogger(__name__)


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
        parts.append(f"[{i}] （{meta['filename']} {page_str}）\n{text}")
        # 细粒度片段：只高亮真正支撑回答的原文句子，而不是整段切片
        span_start, span_len = locate_span(question, text)
        span_text = text[span_start : span_start + span_len] if span_len else ""
        sources.append(
            {
                "index": i,
                "doc_id": meta.get("doc_id"),
                "filename": meta["filename"],
                "page": page or None,
                "chunk_index": meta.get("chunk_index", 0),
                "similarity": hit["similarity"],
                "snippet": (span_text or text)[:120],
                "span_start": span_start,
                "span_len": span_len,
                "span_text": span_text,
                "chunk_char_start": meta.get("char_start"),
                "chunk_char_end": meta.get("char_end"),
            }
        )
    return "\n\n".join(parts), sources


def append_web_results(
    question: str, context: str, sources: list[dict], max_results: int | None = None
) -> tuple[str, list[dict], str | None]:
    """联网时把 Tavily 结果追加为额外上下文（来源标记为 type=web）。

    返回 (新上下文, 来源, 失败原因)；失败原因用于在回答中提示用户。
    """
    hits, error = web_search.search(question, max_results=max_results)
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


def retrieve(
    question: str, top_k: int | None = None, threshold: float | None = None
) -> tuple[str, list[dict]]:
    """检索并构建上下文。无命中时返回空串。"""
    hits = get_vector_store().search(question, top_k=top_k, threshold=threshold)
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
) -> Generator[dict, None, None]:
    """
    完整问答流（供 SSE 使用）：
      先 yield {"type": "sources", ...} 告知引用来源，
      再逐段 yield {"type": "token", ...} 输出回答增量。
    """
    context, sources = retrieve(question, top_k=top_k, threshold=threshold)

    need_web = web_search_enabled and (web_search_mode == "always" or not context)
    web_error = None
    if need_web:
        context, sources, web_error = append_web_results(
            question, context, sources, web_max_results
        )

    yield {"type": "sources", "sources": sources}

    if not context:
        msg = "知识库中未查询到相关内容，请换一种问法，或补充相关文档后再试。"
        if web_error:
            msg += f"\n\n（联网检索未成功：{web_error}）"
        yield {"type": "token", "content": msg}
        yield {"type": "done"}
        return

    from app.services.llm import stream_chat  # 延迟导入，便于测试

    try:
        for token in stream_chat(question, context, temperature=temperature):
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
        logger.exception("LLM 调用失败")
        yield {"type": "error", "message": f"模型调用失败：{e}"}
