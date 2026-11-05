# -*- coding: utf-8 -*-
"""
文档入库流水线：保存文件 -> 解析 -> 清洗 -> 分块 -> 向量化 -> 写入向量库 -> 更新状态。

同步处理，失败自动回滚向量切片，避免脏数据。
"""
import hashlib
import logging
import time
from pathlib import Path

from app.config import settings
from app import crud
from app.services import chunker, parser, settings_service
from app.services.doc_preview import render_doc_html
from app.services.vector import get_vector_store

logger = logging.getLogger(__name__)

# 常见第三方报错 -> 中文提示（前端「处理失败」列与上传接口都会用到）
_ERROR_HINTS = [
    ("batch size is invalid", "向量化批次超过接口上限（单次最多 20 条），请在「设置 → 向量化」把批量大小改为 20 以内后重试"),
    ("should not be larger than", "向量化批次超过接口上限，请在「设置 → 向量化」把批量大小改为 20 以内后重试"),
    ("arrearage", "模型账号额度不足或欠费，请充值后重试"),
    ("rate limit", "请求过于频繁或超出额度，请稍后重试"),
    ("invalid api key", "模型 API Key 无效，请检查「设置 → 模型服务」中的 Key"),
    ("invalidapikey", "模型 API Key 无效，请检查「设置 → 模型服务」中的 Key"),
    ("incorrect api key", "模型 API Key 不正确，请检查「设置 → 模型服务」中的 Key"),
    ("unauthorized", "API Key 未授权或无效，请检查「设置 → 模型服务」中的 Key"),
    (" 401", "API Key 无效或未授权，请检查「设置 → 模型服务」中的 Key"),
    (" 403", "无权访问该模型，请检查账号权限"),
    (" 429", "请求过于频繁或额度用尽，请稍后重试"),
    ("timed out", "请求超时，请检查网络后重试"),
    ("timeout", "请求超时，请检查网络后重试"),
    ("name or service not known", "无法解析接口域名，请检查网络"),
    ("password authentication failed", "数据库认证失败，请检查 DATABASE_URL 的账号密码"),
    ("connection", "网络连接失败，请检查网络后重试"),
    ("ssl", "HTTPS 证书校验失败，请检查网络或代理"),
    ("is not a zip file", "文件已损坏或不是有效的 Word/Excel/PPT 文档"),
    ("unsupported", "不支持的文件格式"),
    ("no such file", "文件不存在或读取失败"),
    ("permission denied", "文件无读取权限"),
    ("jsondecode", "接口返回内容异常（不是合法 JSON）"),
]


def translate_error(exc: Exception) -> str:
    """把英文/技术报错翻译成用户能看懂的中文原因（保留原始片段便于排查）。"""
    raw = str(exc)
    low = raw.lower()
    for needle, zh in _ERROR_HINTS:
        if needle in low:
            return f"{zh}（原始错误：{raw[:180]}）"
    return raw[:300]

# 文件名关键词 -> 默认分类（seed / 首次上传时自动归类）
CATEGORY_RULES = [
    ("后台", "后台管理"),
    ("admin", "后台管理"),
    ("app", "App 用户端"),
    ("用户端", "App 用户端"),
]
DEFAULT_CATEGORY = "通用文档"


def file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def infer_category_id(db, filename: str) -> str:
    """按文件名关键词自动归类，命中不到则归入「通用文档」。"""
    lowered = filename.lower()
    for keyword, name in CATEGORY_RULES:
        if keyword in lowered:
            cat = crud.get_or_create_category(db, name)
            return cat.id
    cat = crud.get_or_create_category(db, DEFAULT_CATEGORY)
    return cat.id


def ingest(
    db,
    *,
    filename: str,
    content: bytes,
    source: str = "upload",
    category_id: str | None = None,
    file_hash: str | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> dict:
    """
    完整入库。返回 {doc_id, filename, chunk_count, elapsed, duplicated}。
    content 为空时抛错；重复文件（hash 命中）直接返回原文档信息。
    """
    if not content:
        raise ValueError("文件内容为空")

    digest = file_hash or file_sha256(content)
    store = get_vector_store()
    existing = crud.find_document_by_hash(db, digest)
    if existing and existing.status == "ready":
        logger.info("跳过重复文档: %s (hash=%s)", filename, digest[:8])
        return {
            "doc_id": existing.id,
            "filename": existing.filename,
            "chunk_count": existing.chunk_count,
            "elapsed": 0,
            "duplicated": True,
        }
    if existing:
        # 上次处理失败的残留记录：清掉旧向量与元数据后重新入库
        store.delete_by_doc(existing.id)
        crud.delete_document(db, existing.id)

    category_id = category_id or infer_category_id(db, filename)
    doc = crud.insert_document(
        db,
        filename=filename,
        file_type=Path(filename).suffix.lower(),
        file_size=len(content),
        category_id=category_id,
        source=source,
        file_hash=digest,
    )

    save_path = settings.UPLOAD_DIR / f"{doc.id}_{filename}"
    save_path.write_bytes(content)

    t0 = time.time()
    try:
        elements = parser.extract_and_clean(save_path)
        if not elements:
            raise ValueError("文档解析后无有效文本内容")
        runtime = settings_service.get_runtime()
        chunks = chunker.chunk_document(
            elements,
            doc.id,
            filename,
            size=chunk_size or runtime.get("chunk_size"),
            overlap=chunk_overlap or runtime.get("chunk_overlap"),
        )
        store.add_chunks(chunks)
        # 顺手保存原文 HTML：云端原文件被回收后，右侧预览仍能展示原文并锚定引用片段
        suffix = Path(filename).suffix.lower()
        preview = render_doc_html(save_path, suffix) if suffix != ".pdf" else ""
        crud.update_document(
            db, doc.id, status="ready", chunk_count=len(chunks), error=None,
            preview_html=preview or None,
        )
        elapsed = round(time.time() - t0, 2)
        logger.info("文档入库成功 %s: %d 切片, %.2fs", filename, len(chunks), elapsed)
        return {
            "doc_id": doc.id,
            "filename": filename,
            "chunk_count": len(chunks),
            "elapsed": elapsed,
            "duplicated": False,
        }
    except Exception as e:
        logger.exception("文档处理失败: %s", filename)
        reason = translate_error(e)
        crud.update_document(db, doc.id, status="failed", error=reason, chunk_count=0)
        try:
            store.delete_by_doc(doc.id)
        except Exception:  # 回滚失败不掩盖原始异常
            logger.warning("回滚向量切片失败: %s", doc.id, exc_info=True)
        raise ValueError(reason) from e


def remove(db, doc_id: str) -> bool:
    """删除文档：向量切片 + 元数据 + 原始文件。"""
    doc = crud.get_document(db, doc_id)
    if not doc:
        return False
    get_vector_store().delete_by_doc(doc_id)
    crud.delete_document(db, doc_id)
    for p in settings.UPLOAD_DIR.glob(f"{doc_id}_*"):
        p.unlink(missing_ok=True)
    logger.info("文档已删除: %s", doc.filename)
    return True


def reindex_document(db, doc, path: Path, *, chunk_size: int | None = None, chunk_overlap: int | None = None) -> dict:
    """
    按当前配置重新切片并向量化单个已入库文档（保留文档记录）。

    与 ingest 不同：复用原 doc_id / 原文件路径，失败时不会删除文档记录，
    只回滚新写入的向量，便于设置页在重建失败时给出具体原因并让用户重试。
    """
    store = get_vector_store()
    t0 = time.time()
    runtime = settings_service.get_runtime()
    try:
        elements = parser.extract_and_clean(path)
        if not elements:
            raise ValueError("文档解析后无有效文本内容")
        chunks = chunker.chunk_document(
            elements,
            doc.id,
            doc.filename,
            size=chunk_size or runtime.get("chunk_size"),
            overlap=chunk_overlap or runtime.get("chunk_overlap"),
        )
    except Exception as e:
        # 解析/分块失败：旧切片仍可用，保留原记录，不标记失败状态
        logger.exception("重新分块失败: %s", doc.filename)
        raise ValueError(translate_error(e)) from e

    try:
        store.delete_by_doc(doc.id)
        store.add_chunks(chunks)
        suffix = Path(doc.filename).suffix.lower()
        preview = render_doc_html(path, suffix) if suffix != ".pdf" else ""
        crud.update_document(
            db, doc.id, status="ready", chunk_count=len(chunks), error=None,
            preview_html=preview or None,
        )
    except Exception as e:
        logger.exception("重建向量失败: %s", doc.filename)
        reason = translate_error(e)
        try:
            store.delete_by_doc(doc.id)
        except Exception:
            logger.warning("回滚向量切片失败: %s", doc.id, exc_info=True)
        crud.update_document(db, doc.id, status="failed", error=reason, chunk_count=0)
        raise ValueError(reason) from e

    elapsed = round(time.time() - t0, 2)
    logger.info("文档重新分块成功 %s: %d 切片, %.2fs", doc.filename, len(chunks), elapsed)
    return {"doc_id": doc.id, "filename": doc.filename, "chunk_count": len(chunks), "elapsed": elapsed}


def rebuild_all(db) -> dict:
    """按当前运行时配置重新切片并向量化全部文档（切换向量化模型 / 调整分块参数后执行）。"""
    from sqlalchemy import select

    from app.models import Document
    from app.services import embeddings

    embeddings.reset()
    docs = db.execute(select(Document)).scalars().all()
    # 先把需要的字段摘出来，避免后续提交导致 ORM 对象失效
    items = [{"id": d.id, "filename": d.filename} for d in docs]

    rebuilt = 0
    failed: list[dict] = []
    for item in items:
        path = settings.UPLOAD_DIR / f"{item['id']}_{item['filename']}"
        if not path.exists():
            failed.append({"filename": item["filename"], "error": "原始文件不存在（云端实例回收后需重新上传）"})
            continue
        doc = crud.get_document(db, item["id"])
        if not doc:
            continue
        try:
            reindex_document(db, doc, path)
            rebuilt += 1
        except Exception as e:
            failed.append({"filename": item["filename"], "error": str(e)})

    logger.info("向量索引重建完成: 成功 %d / 共 %d", rebuilt, len(items))
    return {"total": len(items), "rebuilt": rebuilt, "failed": failed}
