# -*- coding: utf-8 -*-
"""
细粒度引用锚定：从召回切片里挑出真正支撑回答的原文片段（span）。

切片级别定位只能高亮到「整段切片」，容易偏到无关位置；这里用问题与切片句子的
关键词重叠度挑出最相关的一两个句子，返回相对切片文本的字符偏移，前端据此精确高亮。
"""
import re

# 句子切分：到句末标点、分号或换行为止（保留原文偏移）
_SENT_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_STOP = {
    "的", "了", "是", "在", "和", "与", "或", "及", "而", "就", "都", "也", "有", "我", "你", "他",
    "她", "它", "这", "那", "么", "吗", "呢", "啊", "呀", "把", "被", "为", "对", "从", "到", "等",
    "the", "and", "or", "for", "with", "that", "this", "are", "was", "were", "is", "be", "to", "of",
    "in", "on", "as", "at", "by", "it", "an", "a",
}

_MAX_SPAN = 220  # 单个高亮片段的最大字符数
_MIN_SPAN = 24   # 太短的句子向两侧扩展，避免只高亮几个字


def _tokens(text: str) -> set[str]:
    """中文取 2-gram、英文取单词，作为轻量关键词集合（不引入分词依赖）。"""
    text = text.lower()
    out: set[str] = set()
    for seg in _CJK_RE.findall(text):
        for i in range(len(seg) - 1):
            gram = seg[i : i + 2]
            if gram not in _STOP:
                out.add(gram)
    for word in _WORD_RE.findall(text):
        if len(word) >= 2 and word not in _STOP:
            out.add(word)
    return out


def locate_span(question: str, text: str) -> tuple[int, int]:
    """
    在切片文本中定位支撑回答的片段。
    :return: (start, length)，相对 text 的字符偏移；无法判断时返回 (0, 0)
    """
    if not question.strip() or not text.strip():
        return 0, 0

    q_tokens = _tokens(question)
    if not q_tokens:
        return 0, 0

    sentences = [(m.start(), m.end()) for m in _SENT_RE.finditer(text)]
    sentences = [(s, e) for s, e in sentences if text[s:e].strip()]
    if not sentences:
        return 0, 0

    best_i, best_score = -1, 0.0
    for i, (s, e) in enumerate(sentences):
        seg = text[s:e]
        tokens = _tokens(seg)
        if not tokens:
            continue
        # 命中数为主，除以句长平方根，避免超长句靠字数取胜
        score = len(q_tokens & tokens) / (len(tokens) ** 0.5)
        if len(seg.strip()) < 8:
            score *= 0.5
        if score > best_score:
            best_i, best_score = i, score
    if best_i < 0 or best_score <= 0:
        return 0, 0

    # 命中的句子太短时，向后（必要时向前）合并相邻句子，扩大可读范围
    s, e = sentences[best_i]
    while e - s < _MIN_SPAN and e < len(text):
        nxt = next((x for x in sentences if x[0] >= e), None)
        if not nxt:
            break
        e = nxt[1]
    if e - s < _MIN_SPAN and best_i > 0:
        s = sentences[best_i - 1][0]
    # 超长句只取前半段（仍保留原始偏移）
    if e - s > _MAX_SPAN:
        e = s + _MAX_SPAN

    # 去掉首尾空白，保持偏移正确
    raw = text[s:e]
    lead = len(raw) - len(raw.lstrip())
    tail = len(raw) - len(raw.rstrip())
    s += lead
    e -= tail
    return s, max(0, e - s)
