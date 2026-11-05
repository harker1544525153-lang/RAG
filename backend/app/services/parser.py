# -*- coding: utf-8 -*-
"""
文档解析与清洗：支持 PDF / DOCX / TXT / Markdown / PPTX。

选型说明：
- PDF 用 PyMuPDF（fitz）：速度快、支持按页提取，可拿到页码用于溯源。
- DOCX 用 python-docx（比 Docx2txt 更可控，能按段落顺序输出）。
- PPTX 用 python-pptx 按页提取文本框。
- TXT / MD 直接读取。
"""
import logging
import re
from pathlib import Path

import pymupdf as fitz  # PyMuPDF（2.x 推荐 import pymupdf，兼容别名 fitz）

logger = logging.getLogger(__name__)

# 每个元素: {"text": str, "page": int|None}


def extract_elements(file_path: Path) -> list[dict]:
    """解析文件，返回带页码的文本元素列表，供后续分块时写入元数据（用于回答溯源）。"""
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(file_path)
    if suffix == ".docx":
        return _extract_docx(file_path)
    if suffix == ".pptx":
        return _extract_pptx(file_path)
    if suffix in (".txt", ".md"):
        return _extract_text(file_path)
    raise ValueError(f"不支持的文件类型: {suffix}")


def _extract_pdf(file_path: Path) -> list[dict]:
    elements = []
    with fitz.open(str(file_path)) as doc:
        for page_no, page in enumerate(doc, start=1):
            text = page.get_text("text")
            if text.strip():
                elements.append({"text": text, "page": page_no})
    return elements


def _extract_docx(file_path: Path) -> list[dict]:
    try:
        return _extract_docx_python_docx(file_path)
    except Exception as e:
        # 部分 WPS/第三方导出的 docx 存在非法 media part，python-docx 会直接报错，
        # 回退到直接解析 OOXML，保证企业文档可入库。
        logger.warning("python-docx 解析失败，回退 OOXML 文本提取: %s -> %s", file_path.name, e)
        return _extract_docx_ooxml(file_path)


def _extract_docx_python_docx(file_path: Path) -> list[dict]:
    from docx import Document

    doc = Document(str(file_path))
    buf = []
    for para in doc.paragraphs:
        if para.text.strip():
            buf.append(para.text.strip())
    # 表格内容也提取（企业文档中表格信息很关键）
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                buf.append(" | ".join(cells))
    return [{"text": "\n".join(buf), "page": None}] if buf else []


def _extract_docx_ooxml(file_path: Path) -> list[dict]:
    """直接读取 word/document.xml 提取段落文本（不依赖 python-docx 的包校验）。"""
    import zipfile
    from xml.etree import ElementTree as ET

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
    return [{"text": "\n".join(paragraphs), "page": None}] if paragraphs else []


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
            elements.append({"text": "\n".join(texts), "page": slide_no})
    return elements


def _extract_text(file_path: Path) -> list[dict]:
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    return [{"text": text, "page": None}] if text.strip() else []


# ---------------- 清洗 ----------------

# 常见乱码/控制字符/多余空白
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_HYPHEN_LF_RE = re.compile(r"(\w)-\n(\w)")  # 英文换行连字：word-\nbreak -> word-break


def clean_text(raw: str) -> str:
    """清洗：去控制字符/乱码、修复英文断词换行、合并多余空行，保留段落结构。"""
    text = _CTRL_RE.sub("", raw)
    text = text.replace("\ufffd", "")          # U+FFFD 替换符
    text = _HYPHEN_LF_RE.sub(r"\1\2", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_NL_RE.sub("\n\n", text)
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def extract_and_clean(file_path: Path) -> list[dict]:
    """对外入口：解析 + 逐元素清洗。"""
    elements = extract_elements(file_path)
    result = []
    for el in elements:
        text = clean_text(el["text"])
        if text:  # 丢弃清洗后为空的元素
            result.append({"text": text, "page": el["page"]})
    logger.info("解析完成 %s: %d 个有效文本元素", file_path.name, len(result))
    return result
