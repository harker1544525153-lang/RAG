# -*- coding: utf-8 -*-
"""LLM 服务：阿里云百炼 / DeepSeek（OpenAI 兼容协议），单一 .env 配置。"""
import logging
from typing import Generator

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

# ====== 系统提示词（Prompt）：如需修订，直接修改此处 ======
# 联网搜索与本地知识库走同一套 Prompt，因此回复格式统一，不区分是否联网。
SYSTEM_PROMPT = """你只能使用下面参考上下文回答问题，禁止编造上下文不存在的信息。
参考上下文每条以 [编号] 开头，该编号就是引用编号：
{context}
用户问题：{question}
回答要求（无论内容来自知识库还是联网搜索，都按同一格式）：
1. 纯文本作答，不要使用 Markdown 标记（如 **加粗**、`反引号`、# 标题、- 列表符）
2. 结构：先用一句话给出结论，再分点说明，分点用「1. 2. 3.」编号
3. 引用：在对应句子末尾标注 [编号]，编号与参考上下文一致；不要写「（来源：…）」「原文依据」「[[编号]]」，也不要输出网址
4. 多个来源合并作答，按内容逻辑组织，不要按来源类型分段
5. 上下文没有答案时，只回复：【知识库中未查询到相关内容】，不要猜测"""


def _cfg() -> dict:
    """运行时配置（设置页可改，保存后立即生效）。"""
    from app.services import settings_service

    return settings_service.get_runtime()


_client: OpenAI | None = None
_client_key: tuple[str, str] | None = None


def get_client(cfg: dict | None = None) -> OpenAI:
    """按运行时配置创建客户端（复用连接池；Key / URL 变更时自动重建）。"""
    global _client, _client_key
    cfg = cfg or _cfg()
    api_key = (cfg.get("llm_api_key") or "").strip()
    base_url = (cfg.get("llm_base_url") or "").strip()
    if not api_key:
        raise RuntimeError(
            "未配置大模型 API Key，请在管理端「设置 → 模型服务」填写，或在 backend/.env 配置 DASHCOPE_API_KEY"
        )
    cache_key = (api_key, base_url)
    if _client is None or _client_key != cache_key:
        _client = OpenAI(api_key=api_key, base_url=base_url or None)
        _client_key = cache_key
    return _client


def stream_chat(
    question: str,
    context: str,
    *,
    temperature: float | None = None,
    cfg: dict | None = None,
) -> Generator[str, None, None]:
    """流式生成回答。模型、温度与厂商由运行时配置决定。

    cfg 显式传入：SSE 流在线程池中迭代时取不到请求的 contextvar。
    """
    cfg = cfg or _cfg()
    client = get_client(cfg)
    response = client.chat.completions.create(
        model=cfg.get("llm_model") or settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.format(context=context, question=question)},
            {"role": "user", "content": question},
        ],
        temperature=cfg.get("llm_temperature", settings.LLM_TEMPERATURE) if temperature is None else temperature,
        max_tokens=2048,
        stream=True,
    )
    for chunk in response:
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
