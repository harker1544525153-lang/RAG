# -*- coding: utf-8 -*-
"""原文渲染：把上传文件转成网页可见 HTML（尽量保留导入时的原版式）。

入库时生成一次并存入 documents.preview_html，预览页优先读库：
即使 Serverless 实例回收导致 /tmp 原文件丢失，也能展示原文并锚定引用片段。
"""
import html
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Word 里的图片会被 mammoth 转成 base64 内嵌，单篇可放大到 2MB+，预览加载很慢；
# 超过阈值时改为占位符，保留文字与表格结构（图片仍可在原文件中查看）。
MAX_HTML_CHARS = 400_000


def _shrink_html(content: str) -> str:
    if len(content) <= MAX_HTML_CHARS:
        return content
    content = re.sub(
        r'<img[^>]*\ssrc="data:[^"]*"[^>]*>',
        '<span style="color:#86909c">［图片］</span>',
        content,
        flags=re.IGNORECASE,
    )
    return content


def render_doc_html(path: Path, suffix: str, chunks: list[dict] | None = None) -> str:
    """按文件类型转换成网页可见内容：Word 保留文档结构，Markdown 渲染语法，PPT 按页，TXT 保留原排版。"""
    chunks = chunks or []
    try:
        if suffix == ".docx":
            import mammoth

            with open(path, "rb") as f:
                return _shrink_html(mammoth.convert_to_html(f).value)
        if suffix == ".pptx":
            from pptx import Presentation

            parts = []
            for i, slide in enumerate(Presentation(str(path)).slides, 1):
                texts = [
                    f"<p>{html.escape(p.text)}</p>"
                    for shape in slide.shapes
                    if shape.has_text_frame
                    for p in shape.text_frame.paragraphs
                    if p.text.strip()
                ]
                parts.append(
                    f'<section class="slide"><h3>第 {i} 页</h3>'
                    f'{"".join(texts) or "<p>（本页无文本）</p>"}</section>'
                )
            return "".join(parts)
        if suffix in (".md", ".markdown"):
            import markdown

            raw = path.read_text(encoding="utf-8", errors="ignore")
            return markdown.markdown(raw, extensions=["tables", "fenced_code", "nl2br"])
        if suffix == ".pdf":
            return ""
        if suffix == ".txt":
            raw = path.read_text(encoding="utf-8", errors="ignore")
            blocks = [b for b in re.split(r"\n[ \t]*\n", raw) if b.strip()]
            return "".join(f'<p class="plain">{html.escape(b)}</p>' for b in blocks)
        raw = path.read_text(encoding="utf-8", errors="ignore")
        return "".join(f"<p>{html.escape(line)}</p>" for line in raw.splitlines() if line.strip())
    except Exception:
        logger.warning("原文渲染失败: %s", path.name, exc_info=True)
        return ""
