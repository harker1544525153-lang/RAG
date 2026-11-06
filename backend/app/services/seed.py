# -*- coding: utf-8 -*-
"""
初始知识库导入：扫描 DOC_DIR（默认项目根目录 DOC/），
把随仓库提交的企业文档自动解析入库，并按文件名归入分类。

- 以文件 SHA-256 去重，重复启动不会重复导入。
- 启动时在后台线程执行，不阻塞服务启动。
"""
import logging

from app.config import settings
from app.db import SessionLocal
from app import crud
from app.services.ingest import file_sha256, ingest

logger = logging.getLogger(__name__)


def seed_documents(user_id: str | None = None) -> dict:
    """导入 DOC 目录下尚未入库的文档，返回执行摘要。

    user_id 指定归属账号；为空时归属站点默认管理员。
    """
    summary = {"scanned": 0, "imported": 0, "skipped": 0, "failed": 0, "items": []}
    if not settings.DOC_DIR.exists():
        logger.info("初始文档目录不存在，跳过导入: %s", settings.DOC_DIR)
        return summary

    db = SessionLocal()
    try:
        uid = user_id if user_id is not None else (crud.get_default_admin_id(db) or "")
        crud.ensure_default_categories(db, uid)
        for path in sorted(settings.DOC_DIR.iterdir()):
            if not path.is_file() or path.suffix.lower() not in settings.ALLOWED_EXTENSIONS:
                continue
            summary["scanned"] += 1
            try:
                content = path.read_bytes()
                existing = crud.find_document_by_hash(db, file_sha256(content), uid)
                if existing and existing.status == "ready":
                    summary["skipped"] += 1
                    summary["items"].append({"filename": path.name, "status": "skipped"})
                    continue
                result = ingest(db, filename=path.name, content=content, source="seed", user_id=uid)
                summary["imported"] += 1
                summary["items"].append(
                    {"filename": path.name, "status": "imported", "chunk_count": result["chunk_count"]}
                )
                logger.info("初始文档导入成功: %s (%d 切片)", path.name, result["chunk_count"])
            except Exception as e:
                summary["failed"] += 1
                summary["items"].append({"filename": path.name, "status": "failed", "error": str(e)})
                logger.exception("初始文档导入失败: %s", path.name)
        logger.info(
            "初始知识库导入完成：扫描 %d，导入 %d，跳过 %d，失败 %d",
            summary["scanned"],
            summary["imported"],
            summary["skipped"],
            summary["failed"],
        )
        return summary
    finally:
        db.close()
