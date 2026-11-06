# -*- coding: utf-8 -*-
"""
智能分块：按文档分类选择不同策略，解决「一刀切」导致的截断与上下文断裂。

策略（由分类配置，上传时按所选分类生效）：
  token        按 Token 数：以估算 Token 为目标块大小滑动切分（正文的通用兜底）
  standard     基础标准：约 512 Token（中文 1.5 字/Token ≈ 768 字符）+ 10%~15% 重叠
  semantic     语义分块：按标题、段落、条款（第 X 条 / 一、/（一））边界切分
  paragraph    段落结构：标题切章节、段落为最小单位，短段落合并（适配 Word / TXT）
  page         按页切分：同一页内容不跨页混合，保留页码（适配 PDF / PPT）
  hierarchical 层级分块：每个章节生成「章节摘要」父块 + 细粒度子块，兼顾全局与细节
  code         代码结构：按函数 / 类 / 代码块边界切分，保持逻辑完整
  table        表格结构：按行组切分且每块重复表头，避免列语义丢失
  flow         流程结构：按步骤（第 X 步 / 1. / ①）边界切分，保留所属流程与先后顺序

所有切片保留原文 text（用于精确定位），向量化使用 embed_text（可带章节标题增强召回）。
"""
import logging
import re
from typing import Callable

from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]

# 中文约 1.5 字 / Token；英文与代码约 4 字符 / Token
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def estimate_tokens(text: str) -> int:
    """粗略 Token 估算：中文按 1.5 字/Token，其余按 4 字符/Token。"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return max(1, int(cjk / 1.5 + other / 4))


def _chars_for_tokens(tokens: int) -> int:
    """目标 Token 数换算为约等字符数（中文 1.5 字/Token）。"""
    return max(1, int(int(tokens) * 1.5))


def chunk_document(
    elements: list[dict],
    doc_id: str,
    filename: str,
    *,
    strategy: str = "standard",
    size: int | None = None,
    overlap: int | None = None,
    extra: dict | None = None,
    progress_cb: Callable[[int], None] | None = None,
) -> list[dict]:
    """
    将解析后的元素切分为结构化切片。

    :param strategy: token / standard / semantic / paragraph / page / hierarchical / code / table / flow
    :param extra: 文档级结构化元数据（分类、分级、模块、场景、权限、有效期）
    :return: [{"id", "text", "embed_text", "metadata"}]
    """
    strategy = (strategy or "standard").lower()
    if strategy not in (
        "token", "standard", "semantic", "paragraph", "page",
        "hierarchical", "code", "table", "flow",
    ):
        strategy = "standard"
    chunk_size = int(size or 768)
    chunk_overlap = max(0, min(int(overlap or 0), chunk_size - 1))
    # 对外展示的策略名（token 实际复用标准递归切分）
    strategy_label = strategy

    if strategy == "token":
        # 按 Token 数切分：把目标 Token 换算成字符长度后再走递归切分
        chunk_size = _chars_for_tokens(chunk_size)
        chunk_overlap = _chars_for_tokens(chunk_overlap)
        strategy = "standard"

    # 每个元素在整篇文档中的绝对字符起点（元素之间按 1 个换行计）
    offsets = []
    cursor = 0
    for el in elements:
        offsets.append(cursor)
        cursor += len(el["text"]) + 1

    base_meta = {"doc_id": doc_id, "filename": filename, "strategy": strategy_label}
    base_meta.update(extra or {})

    if strategy == "semantic":
        raw = _chunk_semantic(elements, offsets, chunk_size, chunk_overlap)
    elif strategy == "paragraph":
        raw = _chunk_paragraph(elements, offsets, chunk_size, chunk_overlap)
    elif strategy == "page":
        raw = _chunk_page(elements, offsets, chunk_size, chunk_overlap)
    elif strategy == "hierarchical":
        raw = _chunk_hierarchical(elements, offsets, chunk_size, chunk_overlap)
    elif strategy == "code":
        raw = _chunk_code(elements, offsets, chunk_size, chunk_overlap)
    elif strategy == "table":
        raw = _chunk_table(elements, offsets, chunk_size, chunk_overlap)
    elif strategy == "flow":
        raw = _chunk_flow(elements, offsets, chunk_size, chunk_overlap)
    else:
        raw = _chunk_standard(elements, offsets, chunk_size, chunk_overlap)

    chunks: list[dict] = []
    for item in raw:
        text = item["text"].strip()
        if not text:
            continue
        idx = len(chunks)
        meta = dict(base_meta)
        meta.update(
            {
                "page": item.get("page") or 0,
                "chunk_index": idx,
                "char_start": item["char_start"],
                "char_end": item["char_start"] + len(text),
                "section": item.get("section") or "",
                "is_parent": bool(item.get("is_parent")),
                "chunk_kind": item.get("kind") or "text",
                "tokens": estimate_tokens(text),
            }
        )
        section = meta.get("section")
        embed_text = f"{section}\n{text}" if section and not meta["is_parent"] else text
        chunks.append(
            {
                "id": f"{doc_id}_{idx}",
                "text": text,
                "embed_text": embed_text,
                "metadata": meta,
            }
        )
        if progress_cb and idx and idx % 50 == 0:
            progress_cb(idx)

    logger.info(
        "分块完成 %s: %d 个切片 (strategy=%s, size=%d, overlap=%d)",
        filename, len(chunks), strategy_label, chunk_size, chunk_overlap,
    )
    return chunks


# ---------------- 通用工具 --------------

def _splitter(size: int, overlap: int) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        separators=SEPARATORS,
        length_function=len,
        keep_separator=True,
        add_start_index=True,
    )


def _split_long(text: str, size: int, overlap: int) -> list[tuple[str, int]]:
    """超长文本按递归分隔符切分，返回 [(片段, 在原文本中的偏移)]。"""
    pieces = []
    for doc in _splitter(size, overlap).create_documents([text]):
        raw = doc.page_content
        if not raw.strip():
            continue
        lead = len(raw) - len(raw.lstrip())
        pieces.append((raw.strip(), int(doc.metadata.get("start_index", 0)) + lead))
    return pieces


def _chunk_standard(elements, offsets, size, overlap):
    """基础标准：逐元素递归切分，512 Token 基线 + 重叠。"""
    out = []
    for el, base in zip(elements, offsets):
        for text, off in _split_long(el["text"], size, overlap):
            out.append({
                "text": text, "char_start": base + off,
                "page": el.get("page"), "kind": el.get("type", "text"),
            })
    return out


# 段落边界：一个或多个空行
_PARA_BREAK_RE = re.compile(r"\n[ \t]*\n+")


def _paragraph_pieces(text: str) -> list[tuple[str, int]]:
    """按空行拆段落，返回 [(段落文本, 在元素内的偏移)]。"""
    pieces: list[tuple[str, int]] = []
    pos = 0
    for m in _PARA_BREAK_RE.finditer(text):
        seg = text[pos:m.start()]
        start = pos + (len(seg) - len(seg.lstrip()))
        if seg.strip():
            pieces.append((seg.strip(), start))
        pos = m.end()
    seg = text[pos:]
    start = pos + (len(seg) - len(seg.lstrip()))
    if seg.strip():
        pieces.append((seg.strip(), start))
    return pieces


def _chunk_paragraph(elements, offsets, size, overlap):
    """段落结构：标题切章节、段落为最小单位，短段落合并，超长段落再递归切分。"""
    out: list[dict] = []
    section = ""
    buf: list[str] = []
    buf_start = 0
    buf_page = None

    def flush():
        nonlocal buf
        if not buf:
            return
        text = "\n\n".join(buf).strip()
        start = buf_start
        pieces = _split_long(text, size, overlap) if len(text) > size else [(text, 0)]
        for piece, off in pieces:
            out.append({
                "text": piece, "char_start": start + off,
                "page": buf_page, "section": section, "kind": "text",
            })
        buf = []

    for el, base in zip(elements, offsets):
        kind = el.get("type", "text")
        if kind == "table":
            flush()
            out.extend(_chunk_table([el], [base], size, overlap))
            continue
        if kind == "code":
            flush()
            out.extend(_chunk_code([el], [base], size, overlap))
            continue
        if kind == "heading":
            flush()
            section = el["text"].strip()
            buf = [section]
            buf_start = base
            buf_page = el.get("page")
            continue
        for para, off in _paragraph_pieces(el["text"]):
            if not buf:
                buf_start = base + off
                buf_page = el.get("page")
            buf.append(para)
            if sum(len(x) for x in buf) >= size:
                flush()
    flush()
    return out


def _chunk_page(elements, offsets, size, overlap):
    """按页切分：同一页内按段落聚合，跨页不混合；无页码时退化为段落切分。"""
    if not any(el.get("page") for el in elements):
        return _chunk_paragraph(elements, offsets, size, overlap)

    # 先按「连续同页」分组，并记录每页开始时的所属章节
    groups: list[dict] = []
    section = ""
    for el, base in zip(elements, offsets):
        if el.get("type") == "heading":
            section = el["text"].strip()
        page = el.get("page")
        if not groups or page != groups[-1]["page"]:
            groups.append({"page": page, "section": section, "items": []})
        groups[-1]["items"].append((el, base))

    out: list[dict] = []
    for g in groups:
        page = g["page"]
        items = g["items"]
        for item in _chunk_paragraph(
            [e for e, _ in items], [b for _, b in items], size, overlap
        ):
            item["page"] = page or item.get("page")
            if not item.get("section"):
                item["section"] = g["section"]
            out.append(item)
    return out


# 条款 / 章节边界：第X条、第X章、一、、（一）、1.
_CLAUSE_RE = re.compile(
    r"^(第[一二三四五六七八九十百零〇\d]+[条章节编款项]|"
    r"[一二三四五六七八九十]+、|"
    r"（[一二三四五六七八九十]+）|"
    r"\([一二三四五六七八九十]+\)|"
    r"\d+[\.、]\s*)"
)


def _chunk_semantic(elements, offsets, size, overlap):
    """语义分块：标题分段 + 条款边界切分（适配制度 / 合规文档）。"""
    out: list[dict] = []
    section = ""
    buf: list[str] = []
    buf_start = 0
    buf_page = None
    # 条款过短时继续累积，避免切出只有十几个字的碎片
    min_block = max(120, int(size * 0.15))

    def flush():
        nonlocal buf
        if not buf:
            return
        text = "\n".join(buf).strip()
        pieces = _split_long(text, size, overlap) if len(text) > size else [(text, 0)]
        for piece, off in pieces:
            out.append({
                "text": piece, "char_start": buf_start + off,
                "page": buf_page, "section": section, "kind": "text",
            })
        buf = []

    def push(el, base, line_list):
        nonlocal buf, buf_start, buf_page
        for line_text, line_off in line_list:
            if not line_text.strip():
                continue
            if _CLAUSE_RE.match(line_text.strip()) and len("\n".join(buf)) >= min_block:
                flush()
            if not buf:
                buf_start = base + line_off
                buf_page = el.get("page")
            buf.append(line_text.strip())
            if len("\n".join(buf)) >= size:
                flush()

    for el, base in zip(elements, offsets):
        kind = el.get("type", "text")
        if kind == "table":
            flush()
            out.extend(_chunk_table([el], [base], size, overlap))
            continue
        if kind == "code":
            flush()
            out.extend(_chunk_code([el], [base], size, overlap))
            continue
        if kind == "heading":
            flush()
            section = el["text"].strip()
            buf = [section]
            buf_start = base
            buf_page = el.get("page")
            continue
        # 普通段落：按行拆分，识别条款边界
        lines, cursor = [], 0
        for line in el["text"].split("\n"):
            lines.append((line, cursor))
            cursor += len(line) + 1
        push(el, base, lines)
    flush()
    return out


def _chunk_hierarchical(elements, offsets, size, overlap):
    """层级分块：章节摘要父块 + 细粒度子块（子块 embed_text 带章节标题）。"""
    out: list[dict] = []
    sections: list[dict] = []

    def new_section(title, base, page):
        sections.append({"title": title or "", "items": [], "start": base, "page": page})

    new_section("", 0, None)
    for el, base in zip(elements, offsets):
        if el.get("type") == "heading":
            new_section(el["text"].strip(), base, el.get("page"))
            sections[-1]["items"].append(el["text"].strip())
            continue
        if el.get("type") == "table":
            sections[-1]["items"].append(el["text"])
            continue
        sections[-1]["items"].append(el["text"])

    for sec in sections:
        body = "\n".join(x for x in sec["items"] if x).strip()
        if not body:
            continue
        title = sec["title"]
        # 仅标题的根节点（一级标题本身）不单独成块
        if title and body == title:
            continue
        if len(body) <= size:
            # 短章节无需摘要父块，直接一个完整子块，避免重复切片
            out.append({
                "text": body, "char_start": sec["start"],
                "page": sec["page"], "section": title, "kind": "text",
            })
            continue
        # 章节摘要父块：取章节开头内容，保留全局上下文
        summary = body[:240]
        out.append({
            "text": summary, "char_start": sec["start"],
            "page": sec["page"], "section": title, "is_parent": True, "kind": "summary",
        })
        # 细粒度子块
        for piece, off in _split_long(body, size, overlap):
            out.append({
                "text": piece, "char_start": sec["start"] + off,
                "page": sec["page"], "section": title, "kind": "text",
            })
    return out


# 代码块边界：函数 / 类 / 导入 / 顶层声明
_CODE_START_RE = re.compile(
    r"^(?:async\s+)?(?:def|class|function|func|public|private|protected|static|"
    r"export|import|from|package|module\.exports|#include|using|const\s+\w+\s*=|"
    r"var\s+\w+\s*=|let\s+\w+\s*=|@\w+)"
)


def _chunk_code(elements, offsets, size, overlap):
    """代码结构分块：按函数/类/代码块边界聚合，超长再按行打包。"""
    out = []
    for el, base in zip(elements, offsets):
        lines = el["text"].split("\n")
        starts = []
        cursor = 0
        if el.get("type") == "code":
            starts.append((0, 0))
        for i, line in enumerate(lines):
            if _CODE_START_RE.match(line) and i > 0:
                starts.append((i, cursor))
            cursor += len(line) + 1
        if len(starts) <= 1:
            for piece, off in _split_long(el["text"], size, overlap):
                out.append({"text": piece, "char_start": base + off,
                            "page": el.get("page"), "kind": "code"})
            continue

        starts.append((len(lines), cursor))
        for (s_i, s_off), (e_i, _e_off) in zip(starts, starts[1:]):
            block = "\n".join(lines[s_i:e_i]).strip()
            if not block:
                continue
            if len(block) <= size:
                out.append({"text": block, "char_start": base + s_off,
                            "page": el.get("page"), "kind": "code"})
            else:
                for piece, off in _split_long(block, size, overlap):
                    out.append({"text": piece, "char_start": base + s_off + off,
                                "page": el.get("page"), "kind": "code"})
    return out


def _chunk_table(elements, offsets, size, overlap):
    """表格结构分块：表头 + 行组，每块重复表头保证列语义。"""
    out = []
    section = ""
    for el, base in zip(elements, offsets):
        kind = el.get("type", "text")
        if kind == "heading":
            section = el["text"].strip()
            continue
        rows = el.get("rows") or []
        if kind != "table" and not rows:
            # 表格文档中的说明段落：按普通文本切分，并带上所属章节
            for piece, off in _split_long(el["text"], size, overlap):
                out.append({"text": piece, "char_start": base + off,
                            "page": el.get("page"), "section": section, "kind": "text"})
            continue
        if not rows:
            rows = [
                [c.strip() for c in line.strip().strip("|").split("|")]
                for line in el["text"].split("\n")
                if line.strip()
            ]
        if not rows:
            continue
        header = rows[0]
        data = rows[1:] if len(rows) > 1 else []
        avg_len = max(8, sum(len(" | ".join(r)) for r in rows) // max(1, len(rows)))
        per_chunk = max(1, (size // avg_len) - 1)

        def block(row_group, label=""):
            lines = [" | ".join(header)] + [" | ".join(r) for r in row_group]
            return f"{label}\n" + "\n".join(lines) if label else "\n".join(lines)

        if not data:
            out.append({"text": block([]), "char_start": base,
                        "page": el.get("page"), "section": section, "kind": "table"})
            continue
        for i in range(0, len(data), per_chunk):
            group = data[i : i + per_chunk]
            label = f"表格第 {i + 1}-{i + len(group)} 行"
            text = block(group, label)
            if len(text) > size and len(group) > 1:
                mid = max(1, len(group) // 2)
                for g in (group[:mid], group[mid:]):
                    out.append({"text": block(g, label), "char_start": base,
                                "page": el.get("page"), "section": section, "kind": "table"})
            else:
                out.append({"text": text, "char_start": base,
                            "page": el.get("page"), "section": section, "kind": "table"})
    return out


# 流程步骤边界：第X步 / 步骤X / Step N / Stage N / 1. / (1) / ①
_STEP_RE = re.compile(
    r"^(第[一二三四五六七八九十百零〇\d]+步|步骤[一二三四五六七八九十百零〇\d]*|"
    r"step\s*\d+|stage\s*\d+|阶段[一二三四五六七八九十百零〇\d]*|"
    r"\d+[\.、)）]\s*|（\d+）|[(（][一二三四五六七八九十]+[)）]|"
    r"[①②③④⑤⑥⑦⑧⑨⑩])",
    re.I,
)


def _chunk_flow(elements, offsets, size, overlap):
    """流程结构分块：按步骤边界切分，每块保留所属流程标题，避免步骤被截断。"""
    out: list[dict] = []
    section = ""
    buf: list[str] = []
    buf_start = 0
    buf_page = None
    # 标题单独占一行：buf 中出现第 2 行正文后，遇到步骤边界即切开（一步一块）

    def flush():
        nonlocal buf
        if not buf:
            return
        text = "\n".join(buf).strip()
        pieces = _split_long(text, size, overlap) if len(text) > size else [(text, 0)]
        for piece, off in pieces:
            out.append({
                "text": piece, "char_start": buf_start + off,
                "page": buf_page, "section": section, "kind": "text",
            })
        buf = []

    for el, base in zip(elements, offsets):
        kind = el.get("type", "text")
        if kind == "table":
            flush()
            out.extend(_chunk_table([el], [base], size, overlap))
            continue
        if kind == "code":
            flush()
            out.extend(_chunk_code([el], [base], size, overlap))
            continue
        if kind == "heading":
            flush()
            section = el["text"].strip()
            buf = [section]
            buf_start = base
            buf_page = el.get("page")
            continue

        cursor = 0
        for line in el["text"].split("\n"):
            line_off = cursor
            cursor += len(line) + 1
            stripped = line.strip()
            if not stripped:
                continue
            if _STEP_RE.match(stripped) and len(buf) > 1:
                flush()
            if not buf:
                buf_start = base + line_off
                buf_page = el.get("page")
            buf.append(stripped)
            if len("\n".join(buf)) >= size:
                flush()
    flush()
    return out

