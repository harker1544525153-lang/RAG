# -*- coding: utf-8 -*-
"""
文本分块：基于 LangChain RecursiveCharacterTextSplitter，
按 \n\n -> \n -> 。！？ -> ；， -> 空格 的优先级逐步切分，
chunk_overlap 约 20%，防止跨段语义断裂。

chunk_size / chunk_overlap 支持运行时传入（设置页可调，仅影响新上传文档）。
"""
import logging
from typing import Callable

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings

logger = logging.getLogger(__name__)


def _build_splitter(size: int, overlap: int) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
        length_function=len,
        keep_separator=True,
        add_start_index=True,
    )


def chunk_document(
    elements: list[dict],
    doc_id: str,
    filename: str,
    *,
    size: int | None = None,
    overlap: int | None = None,
    progress_cb: Callable[[int], None] | None = None,
) -> list[dict]:
    """
    将解析后的文本元素切分为结构化切片。

    :return: 切片列表 [{"id", "text", "metadata": {doc_id, filename, page, chunk_index}}]
    """
    chunk_size = size or settings.CHUNK_SIZE
    chunk_overlap = overlap or settings.CHUNK_OVERLAP
    splitter = _build_splitter(chunk_size, chunk_overlap)
    chunks: list[dict] = []
    global_index = 0
    # 文档全文中的字符偏移（按元素顺序累计；元素之间按 1 个换行计）
    doc_offset = 0
    for el in elements:
        el_text = el["text"]
        # create_documents 会带上 add_start_index 的 start_index，用于换算原文偏移
        for piece in splitter.create_documents([el_text]):
            raw = piece.page_content
            text = raw.strip()
            if not text:
                continue
            lead = len(raw) - len(raw.lstrip())
            char_start = doc_offset + int(piece.metadata.get("start_index", 0)) + lead
            chunks.append(
                {
                    "id": f"{doc_id}_{global_index}",
                    "text": text,
                    "metadata": {
                        "doc_id": doc_id,
                        "filename": filename,
                        "page": el["page"] if el["page"] is not None else 0,
                        "chunk_index": global_index,
                        "char_start": char_start,
                        "char_end": char_start + len(text),
                    },
                }
            )
            global_index += 1
            if progress_cb and global_index % 50 == 0:
                progress_cb(global_index)
        doc_offset += len(el_text) + 1
    logger.info(
        "分块完成 %s: %d 个切片 (size=%d, overlap=%d)",
        filename,
        len(chunks),
        chunk_size,
        chunk_overlap,
    )
    return chunks
