# -*- coding: utf-8 -*-
"""系统设置接口：读取/更新运行时参数，分块或向量化配置变更后自动重建切片，手动导入初始文档。"""
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import ingest, settings_service
from app.services.seed import seed_documents

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    """当前生效配置（密钥字段回显已保存值或 .env 默认值，未配置为空）+ 只读系统信息。"""
    settings_service.refresh(db)
    return settings_service.describe(db)


@router.put("")
def update_settings(payload: dict[str, Any], db: Session = Depends(get_db)):
    """
    保存设置：白名单字段生效，留空的密钥表示不修改；保存后立即生效。

    分块大小/重叠或向量化配置有变化时，立即按新配置重建全部文档分块；
    重建失败返回具体原因（前端提示「保存失败：…」），配置本身已落库，可修正后重试。
    """
    try:
        result = settings_service.update(db, payload)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))

    rebuild = None
    if result["rebuild_required"]:
        try:
            rebuild = ingest.rebuild_all(db)
        except Exception as e:
            logger.exception("保存设置后重建文档分块失败")
            raise HTTPException(500, f"重建文档分块失败：{e}")
        if rebuild["failed"]:
            detail = "；".join(
                f"{x['filename']}：{x['error']}" for x in rebuild["failed"][:3]
            )
            rest = f"（共 {len(rebuild['failed'])} 篇失败）" if len(rebuild["failed"]) > 3 else ""
            raise HTTPException(500, f"重建文档分块失败：{detail}{rest}")

    body = settings_service.describe(db)
    body["rebuild_required"] = False
    if rebuild:
        body["rebuild_result"] = rebuild
    return body


@router.post("/rebuild-vectors")
def rebuild_vectors(db: Session = Depends(get_db)):
    """按当前向量化配置重新生成全部切片向量（切换模型后调用）。"""
    try:
        return ingest.rebuild_all(db)
    except Exception as e:
        raise HTTPException(500, f"重建索引失败: {e}")


@router.post("/seed")
def import_seed_documents():
    """手动触发 DOC 目录导入（按文件哈希去重，已入库的会跳过）。"""
    summary = seed_documents()
    return summary
