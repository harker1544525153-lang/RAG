# -*- coding: utf-8 -*-
"""
BM25 关键词检索（混合检索的稀疏侧）。

中文分词策略（按优先级）：
1. 已安装 jieba 时使用 `cut_for_search` 搜索模式：长词会被额外切成子词，
   兼顾「精确命中文档标题词」与「召回同义片段」，是检索场景的推荐用法。
2. 未安装 jieba 时回退到改进版字符 bigram：按标点/非中文边界断开后取二元组，
   单字片段保留单字，并过滤疑问词/助词等停用词，避免「有什 / 么功」这类跨词噪声。
3. 英文与数字始终按完整词保留（`LangChain`、`GB-2025-01` 不被拆散），
   并提供编辑距离 ≤ 1 的模糊匹配，用于容忍拼写差异（如 longchain / langchain）。

其他：
- 语料来自向量库同一批切片（只读），进程内缓存 60 秒或切片数变化时重建。
- 与向量检索结果在 rag 层用 RRF（Reciprocal Rank Fusion）融合。
"""
import logging
import math
import re
import threading
import time

logger = logging.getLogger(__name__)

try:  # jieba 为可选依赖：缺失时自动回退到 bigram，不影响服务启动
    import jieba

    jieba.setLogLevel(logging.WARNING)
    _JIEBA = jieba
except Exception:  # pragma: no cover - 环境缺少 jieba 时走回退分支
    _JIEBA = None

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_EN_WORD = re.compile(r"[a-z][a-z0-9_\-]{2,}")
_EN_TERM = re.compile(r"[a-zA-Z][a-zA-Z0-9_\-]{2,}")
_QUOTED = re.compile(r"[“\"'「『《]([^”\"'」』》]{1,20})[”\"'」』》]")
_MEANINGFUL = re.compile(r"[a-z0-9\u4e00-\u9fff]")

# 停用词：单字助词/语气词 + 常见疑问词与泛指词，避免它们主导 BM25 打分
_STOP = {
    # 单字
    "的", "了", "是", "在", "有", "和", "与", "及", "或", "对", "为", "从", "按",
    "把", "被", "让", "给", "就", "都", "也", "还", "而", "并", "等", "着", "过",
    "吗", "呢", "吧", "啊", "呀", "哦", "么", "之", "其", "这", "那", "我", "你",
    "他", "她", "它", "们",
    # 疑问 / 泛指
    "什么", "怎么", "怎样", "如何", "哪些", "哪个", "哪里", "为何", "为什么",
    "是否", "多少", "请问", "可以", "能否", "需要", "进行", "一个", "这个",
    "那个", "这些", "那些", "我们", "你们", "他们", "一下", "以及", "还有",
    "关于", "目前", "现在", "主要", "相关",
    # 英文
    "a", "an", "the", "is", "are", "was", "were", "be", "of", "to", "in", "on",
    "at", "by", "for", "and", "or", "not", "with", "as", "it", "this", "that",
    "what", "how", "which", "who", "can", "do", "does",
}

# BM25 参数（经典取值）
_K1 = 1.5
_B = 0.75
_TTL = 60.0

_lock = threading.Lock()
_cache: dict = {"key": None, "ts": 0.0, "docs": [], "tf": [], "df": {}, "avgdl": 0.0}


def _fallback_tokens(low: str) -> list[str]:
    """无 jieba 时的回退分词：中文 bigram + 单字，英文/数字整词。"""
    tokens: list[str] = []
    for seg in _CJK.findall(low):
        if len(seg) == 1:
            if seg not in _STOP:
                tokens.append(seg)
        else:
            for i in range(len(seg) - 1):
                gram = seg[i : i + 2]
                if gram not in _STOP:
                    tokens.append(gram)
    for m in _EN_WORD.finditer(low):
        word = m.group()
        if word not in _STOP:
            tokens.append(word)
    return tokens


def tokenize(text: str) -> list[str]:
    """中英混合分词：jieba 搜索模式优先，缺失时回退改进版 bigram。"""
    if not text:
        return []
    low = text.lower()
    tokens: list[str] = []

    if _JIEBA is not None:
        for word in _JIEBA.cut_for_search(low):
            word = word.strip()
            if not word or word in _STOP:
                continue
            # 丢弃纯标点/空白产生的 token
            if not _MEANINGFUL.search(word):
                continue
            tokens.append(word)
        # jieba 会把英文词整体保留；仅在 jieba 未输出该词时补充，避免重复放大词频
        emitted = set(tokens)
        tokens.extend(
            m.group()
            for m in _EN_WORD.finditer(low)
            if m.group() not in _STOP and m.group() not in emitted
        )
    else:
        tokens = _fallback_tokens(low)

    return tokens


def extract_terms(question: str) -> list[str]:
    """抽取查询中的强关键词：英文术语、编号型号、引号内短语。"""
    terms: set[str] = set()
    for m in _EN_TERM.findall(question or ""):
        term = m.lower()
        if term not in _STOP:
            terms.add(term)
    for m in _QUOTED.findall(question or ""):
        term = m.strip().lower()
        if term and term not in _STOP:
            terms.add(term)
    return sorted(terms)


def _edit_distance_le1(a: str, b: str) -> bool:
    """判断两个词是否只差一个字符（容忍 longchain / langchain 这类拼写差异）。"""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    i = j = 0
    diff = 0
    while i < len(a) and j < len(b):
        if a[i] != b[j]:
            diff += 1
            if diff > 1:
                return False
            if len(a) > len(b):
                i += 1
            elif len(b) > len(a):
                j += 1
            else:
                i += 1
                j += 1
        else:
            i += 1
            j += 1
    return True


def keyword_match(text: str, terms: list[str]) -> list[str]:
    """返回切片文本中命中的关键词（含长度≥4 英文词的模糊匹配）。"""
    if not terms:
        return []
    low = (text or "").lower()
    hits: list[str] = []
    words: set[str] | None = None
    for term in terms:
        if term in low:
            hits.append(term)
            continue
        if len(term) >= 4 and term.isascii():
            if words is None:
                words = set(_EN_WORD.findall(low))
            if any(
                abs(len(w) - len(term)) <= 1 and _edit_distance_le1(term, w)
                for w in words
            ):
                hits.append(term)
    return hits


def _build(docs: list[dict]) -> dict:
    """构建倒排统计：文档词频、词文档频率、平均长度。"""
    tf_list = []
    df: dict[str, int] = {}
    total_len = 0
    for doc in docs:
        tokens = tokenize(doc.get("text") or "")
        tf: dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        tf_list.append(tf)
        total_len += len(tokens)
        for t in tf:
            df[t] = df.get(t, 0) + 1
    avgdl = (total_len / len(docs)) if docs else 0.0
    return {"tf": tf_list, "df": df, "avgdl": avgdl}


def _get_corpus(store) -> tuple[list[dict], dict]:
    """取切片语料并按需重建索引（切片数变化或缓存过期时重建）。"""
    global _cache
    try:
        count = store.count()
    except Exception:
        count = -1
    key = f"{store.name}:{count}"
    now = time.time()
    with _lock:
        if _cache["key"] == key and (now - _cache["ts"]) < _TTL:
            return _cache["docs"], _cache
        docs = store.all_chunks()
        built = _build(docs)
        _cache = {"key": key, "ts": now, "docs": docs, **built}
        logger.info(
            "BM25 索引已重建：%d 个切片（分词=%s）",
            len(docs),
            "jieba" if _JIEBA is not None else "bigram",
        )
        return _cache["docs"], _cache


def search(question: str, store, top_k: int) -> list[dict]:
    """BM25 关键词召回，返回与向量检索同构的 hit 列表（额外带 bm25 原始分）。"""
    docs, idx = _get_corpus(store)
    if not docs:
        return []

    q_tokens = tokenize(question)
    if not q_tokens:
        return []

    n = len(docs)
    avgdl = idx["avgdl"] or 1.0
    df = idx["df"]

    # 英文术语拼写模糊扩展：longchain 同时匹配 langchain 这类差一个字符的写法
    variants = []
    for term in q_tokens:
        if len(term) >= 4 and term.isascii():
            for word in df:
                if (
                    len(word) >= 4
                    and word.isascii()
                    and abs(len(word) - len(term)) <= 1
                    and _edit_distance_le1(term, word)
                ):
                    variants.append(word)
    if variants:
        q_tokens = list(dict.fromkeys(q_tokens + variants))

    scores: list[tuple[float, int]] = []
    for i, tf in enumerate(idx["tf"]):
        dl = sum(tf.values()) or 1
        score = 0.0
        for t in q_tokens:
            freq = tf.get(t)
            if not freq:
                continue
            idf = math.log(1.0 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
            score += idf * (freq * (_K1 + 1)) / (freq + _K1 * (1 - _B + _B * dl / avgdl))
        if score > 0:
            scores.append((score, i))

    scores.sort(reverse=True)
    hits = []
    for score, i in scores[:top_k]:
        doc = docs[i]
        hits.append(
            {
                "text": doc.get("text", ""),
                "metadata": doc.get("metadata", {}),
                "similarity": None,
                "bm25": round(score, 4),
            }
        )
    logger.info("BM25 检索 '%s': 分词 %s，命中 %d 条", question[:30], q_tokens, len(hits))
    return hits


def clear() -> None:
    """文档增删/重建后主动失效缓存（可选调用）。"""
    global _cache
    with _lock:
        _cache = {"key": None, "ts": 0.0, "docs": [], "tf": [], "df": {}, "avgdl": 0.0}
