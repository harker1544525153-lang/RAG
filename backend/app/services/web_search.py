# -*- coding: utf-8 -*-
"""Tavily 联网搜索：把网页结果转成可引用的上下文片段。

设计原则：
- Key / 地址 / 条数支持管理端「设置 → 模型服务」运行时配置（.env 为默认回退）
- 任何网络异常都不应中断问答：失败时记录日志并返回空列表
"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _cfg() -> dict:
    from app.services import settings_service

    return settings_service.get_runtime()


def search(question: str, max_results: int | None = None) -> list[dict]:
    """调用 Tavily 搜索，返回 [{title, url, content}]；未配置 Key 时返回空。"""
    cfg = _cfg()
    api_key = (cfg.get("tavily_api_key") or "").strip()
    if not api_key:
        logger.warning("未配置 Tavily API Key，联网搜索已跳过（可在管理端「设置」填写）")
        return []

    base_url = (cfg.get("tavily_base_url") or settings.TAVILY_BASE_URL).rstrip("/")
    payload = {
        "api_key": api_key,
        "query": question,
        "max_results": max_results
        or int(cfg.get("web_search_max_results") or settings.TAVILY_MAX_RESULTS),
        "search_depth": "basic",
        "include_answer": False,
        "include_raw_content": False,
    }
    try:
        resp = httpx.post(f"{base_url}/search", json=payload, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # 网络/额度/Key 错误均降级为「不联网」
        logger.warning("Tavily 联网搜索失败：%s", e)
        return []

    results = []
    for item in data.get("results", []):
        content = (item.get("content") or "").strip()
        if not content:
            continue
        results.append(
            {
                "title": item.get("title") or "网页结果",
                "url": item.get("url") or "",
                "content": content,
            }
        )
    logger.info("Tavily 联网搜索完成：%d 条结果", len(results))
    return results
