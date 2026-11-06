# -*- coding: utf-8 -*-
"""
文档解析与清洗：支持 PDF / DOCX / TXT / Markdown / PPTX / 常见代码文件。

结构化输出（供分块策略按边界切割）：
  {"type": "text"|"heading"|"table"|"code", "text": str, "page": int|None,
   "level": int, "rows": list[list[str]]}

- DOCX 识别标题样式与表格结构，按文档真实顺序输出（表格不再被拍平成普通文本）。
- Markdown / TXT 识别 # 标题、``` 代码块、| 表格。
- 代码文件整体标记为 code，保留缩进，供「按函数/类边界」切分。
"""
import logging
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pymupdf as fitz  # PyMuPDF（2.x 推荐 import pymupdf，兼容别名 fitz）

logger = logging.getLogger(__name__)

CODE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".cpp", ".c",
    ".cs", ".php", ".rb", ".sql", ".sh", ".ps1", ".json", ".yaml", ".yml",
    ".html", ".css", ".vue", ".xml",
}


def _el(text: str, *, page=None, type_="text", level=0, rows=None) -> dict:
    return {"text": text, "page": page, "type": type_, "level": level, "rows": rows or []}


def extract_elements(file_path: Path) -> list[dict]:
    """解析文件，返回带类型/页码的文本元素列表。"""
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(file_path)
    if suffix == ".docx":
        return _extract_docx(file_path)
    if suffix == ".pptx":
        return _extract_pptx(file_path)
    if suffix in CODE_EXTS:
        return _extract_code(file_path)
    if suffix in (".txt", ".md", ".markdown"):
        return _extract_text(file_path)
    raise ValueError(f"不支持的文件类型: {suffix}")


def _extract_pdf(file_path: Path) -> list[dict]:
    elements = []
    with fitz.open(str(file_path)) as doc:
        for page_no, page in enumerate(doc, start=1):
            text = page.get_text("text")
            if text.strip():
                elements.append(_el(text, page=page_no))
    return elements


def _heading_level(para) -> int:
    """识别 Word 标题：样式名（Heading 1 / 标题 1）或大纲级别。"""
    try:
        style = (para.style.name or "").strip()
    except Exception:
        style = ""
    m = re.search(r"(\d+)", style)
    if style.lower().startswith(("heading", "标题")) and m:
        return max(1, min(6, int(m.group(1))))
    try:
        ppr = para._p.pPr
        if ppr is not None and ppr.outlineLvl is not None:
            return max(1, min(6, int(ppr.outlineLvl.val) + 1))
    except Exception:
        pass
    return 0


def _extract_docx(file_path: Path) -> list[dict]:
    try:
        return _extract_docx_python_docx(file_path)
    except Exception as e:
        logger.warning("python-docx 解析失败，回退 OOXML 文本提取: %s -> %s", file_path.name, e)
        return _extract_docx_ooxml(file_path)


def _iter_docx_blocks(doc):
    """按正文真实顺序交替产出段落与表格（python-docx 默认两套集合会丢顺序）。"""
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


def _extract_docx_python_docx(file_path: Path) -> list[dict]:
    from docx import Document

    doc = Document(str(file_path))
    elements: list[dict] = []
    for block in _iter_docx_blocks(doc):
        if hasattr(block, "rows"):  # 表格
            rows = []
            for row in block.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    rows.append(cells)
            if rows:
                # 表格按行保存，分块时重复表头，避免列语义断裂
                elements.append(_el("\n".join(" | ".join(r) for r in rows), type_="table", rows=rows))
        else:
            text = block.text.strip()
            if not text:
                continue
            level = _heading_level(block)
            elements.append(_el(text, type_="heading" if level else "text", level=level))
    return elements


def _extract_docx_ooxml(file_path: Path) -> list[dict]:
    """直接读取 word/document.xml 提取段落文本（不依赖 python-docx 的包校验）。"""
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(file_path) as zf:
        with zf.open("word/document.xml") as fp:
            root = ET.parse(fp).getroot()

    paragraphs = []
    for para in root.iter(f"{ns}p"):
        texts = [node.text for node in para.iter(f"{ns}t") if node.text]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    return [_el("\n".join(paragraphs))] if paragraphs else []


def _extract_pptx(file_path: Path) -> list[dict]:
    from pptx import Presentation

    prs = Presentation(str(file_path))
    elements = []
    for slide_no, slide in enumerate(prs.slides, start=1):
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                t = shape.text_frame.text.strip()
                if t:
                    texts.append(t)
        if texts:
            elements.append(_el("\n".join(texts), page=slide_no))
    return elements


def _extract_code(file_path: Path) -> list[dict]:
    raw = file_path.read_text(encoding="utf-8", errors="ignore")
    return [_el(raw, type_="code")] if raw.strip() else []


# Markdown / TXT 结构识别
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_MD_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$")
_FENCE_RE = re.compile(r"^\s*```")


def _extract_text(file_path: Path) -> list[dict]:
    raw = file_path.read_text(encoding="utf-8", errors="ignore")
    if not raw.strip():
        return []

    elements: list[dict] = []
    buf: list[str] = []
    code_buf: list[str] = []
    table_buf: list[str] = []
    in_code = False

    def flush_text():
        if buf:
            elements.append(_el("\n".join(buf)))
            buf.clear()

    def flush_table():
        if table_buf:
            rows = [
                [c.strip() for c in line.strip().strip("|").split("|")]
                for line in table_buf
                if not re.match(r"^\s*\|?\s*[-: |]+\s*\|?\s*$", line)
            ]
            text = "\n".join(" | ".join(r) for r in rows)
            if text.strip():
                elements.append(_el(text, type_="table", rows=rows))
            table_buf.clear()

    for line in raw.splitlines():
        if _FENCE_RE.match(line):
            if in_code:
                if code_buf:
                    elements.append(_el("\n".join(code_buf), type_="code"))
                    code_buf.clear()
                in_code = False
            else:
                flush_text()
                in_code = True
            continue
        if in_code:
            code_buf.append(line)
            continue
        m = _MD_HEADING_RE.match(line)
        if m:
            flush_table()
            flush_text()
            elements.append(_el(m.group(2).strip(), type_="heading", level=len(m.group(1)))
                            )
            continue
        if _MD_TABLE_RE.match(line):
            flush_text()
            table_buf.append(line)
            continue
        flush_table()
        if line.strip():
            buf.append(line)
        else:
            flush_text()

    flush_table()
    flush_text()
    if code_buf:
        elements.append(_el("\n".join(code_buf), type_="code"))
    return elements


# ---------------- 清洗 ----------------

# 常见乱码/控制字符/多余空白
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_HYPHEN_LF_RE = re.compile(r"(\w)-\n(\w)")  # 英文换行连字：word-\nbreak -> word-break


def clean_text(raw: str) -> str:
    """清洗普通正文：去控制字符/乱码、修复英文断词换行、合并多余空行，保留段落结构。"""
    text = _CTRL_RE.sub("", raw)
    text = text.replace("\ufffd", "")          # U+FFFD 替换符
    text = _HYPHEN_LF_RE.sub(r"\1\2", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_NL_RE.sub("\n\n", text)
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def _clean_code(raw: str) -> str:
    """代码只去控制字符，保留缩进与空行（缩进是代码语义的一部分）。"""
    return _CTRL_RE.sub("", raw.replace("\r\n", "\n").replace("\r", "\n")).strip("\n")


def extract_and_clean(file_path: Path) -> list[dict]:
    """对外入口：解析 + 按元素类型清洗（代码保留缩进）。"""
    elements = extract_elements(file_path)
    result = []
    for el in elements:
        kind = el.get("type", "text")
        text = _clean_code(el["text"]) if kind == "code" else clean_text(el["text"])
        if text:
            result.append(
                {
                    "text": text,
                    "page": el.get("page"),
                    "type": kind,
                    "level": el.get("level", 0),
                    "rows": el.get("rows", []),
                }
            )
    logger.info("解析完成 %s: %d 个有效文本元素", file_path.name, len(result))
    return result
