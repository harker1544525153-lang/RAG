# -*- coding: utf-8 -*-
"""系统设置接口：按账号读写运行时参数（含品牌标识），配置变更后重建该账号的文档切片。"""
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app import crud
from app.db import get_db
from app.models import User
from app.security import create_widget_token, optional_user, require_user
from app.services import ingest, settings_service
from app.services.seed import seed_documents

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/widget-link")
def my_widget_link(user: User = Depends(require_user)):
    """当前账号的用户端问答链接：访客免登录打开，问答只用该账号的文档与模型配置。"""
    token = create_widget_token(user.id)
    return {"token": token, "url": f"/user/index.html?at={token}"}

_LOGO_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
_LOGO_MAX_BYTES = 2 * 1024 * 1024
_BRAND_DIR = settings.DATA_DIR / "brand"


def _brand_payload(runtime: dict) -> dict:
    return {
        "admin_title": runtime.get("brand_admin_title") or "GALA 管理后台",
        "admin_subtitle": runtime.get("brand_admin_subtitle") or "",
        "admin_logo": runtime.get("brand_admin_logo") or "",
        "user_title": runtime.get("brand_user_title") or "GALA 智能问答助手",
        "user_subtitle": runtime.get("brand_user_subtitle") or "",
        "user_logo": runtime.get("brand_user_logo") or "",
    }


@router.get("")
def get_settings(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """当前账号生效配置（密钥字段回显已保存值或 .env 默认值，未配置为空）+ 只读系统信息。"""
    settings_service.refresh(db, user.id)
    return settings_service.describe(db, user.id)


@router.put("")
def update_settings(
    payload: dict[str, Any],
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    保存设置：白名单字段生效，留空的密钥表示不修改；保存后立即生效。

    分块大小/重叠或向量化配置有变化时，立即按新配置重建当前账号的文档分块；
    重建失败返回具体原因（前端提示「保存失败：…」），配置本身已落库，可修正后重试。
    """
    try:
        result = settings_service.update(db, payload, user.id)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))

    rebuild = None
    if result["rebuild_required"]:
        try:
            rebuild = ingest.rebuild_all(db, user.id)
        except Exception as e:
            logger.exception("保存设置后重建文档分块失败")
            raise HTTPException(500, f"重建文档分块失败：{e}")
        if rebuild["failed"]:
            detail = "；".join(
                f"{x['filename']}：{x['error']}" for x in rebuild["failed"][:3]
            )
            rest = f"（共 {len(rebuild['failed'])} 篇失败）" if len(rebuild["failed"]) > 3 else ""
            raise HTTPException(500, f"重建文档分块失败：{detail}{rest}")

    body = settings_service.describe(db, user.id)
    body["rebuild_required"] = False
    if rebuild:
        body["rebuild_result"] = rebuild
    return body


@router.post("/rebuild-vectors")
def rebuild_vectors(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """按当前向量化配置重新生成该账号全部切片的向量（切换模型后调用）。"""
    try:
        return ingest.rebuild_all(db, user.id)
    except Exception as e:
        raise HTTPException(500, f"重建索引失败: {e}")


@router.post("/seed")
def import_seed_documents(
    user: User = Depends(require_user), db: Session = Depends(get_db)
):
    """手动触发 DOC 目录导入（按文件哈希去重，已入库的会跳过）。"""
    return seed_documents(user_id=user.id)


@router.post("/brand-logo")
def upload_brand_logo(
    kind: str = Form("user"),
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """上传用户端 / 后台管理的 Logo，并写入当前账号的品牌设置。"""
    if kind not in ("admin", "user"):
        raise HTTPException(400, "Logo 类型仅支持 admin / user")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _LOGO_SUFFIXES:
        raise HTTPException(400, "仅支持 PNG / JPG / SVG / WEBP 图片")
    content = file.file.read()
    if not content:
        raise HTTPException(400, "图片内容为空")
    if len(content) > _LOGO_MAX_BYTES:
        raise HTTPException(400, "图片不能超过 2MB")

    _BRAND_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{user.id}_{kind}{suffix}"
    target = _BRAND_DIR / filename
    target.write_bytes(content)
    # 同名旧文件（扩展名不同）清理，避免残留
    for p in _BRAND_DIR.glob(f"{user.id}_{kind}.*"):
        if p != target:
            p.unlink(missing_ok=True)

    key = f"brand_{kind}_logo"
    url = f"/brand/{filename}"
    crud.set_settings(db, {key: url}, user.id)
    settings_service.refresh(db, user.id)
    return {"key": key, "url": url}


@router.delete("/brand-logo")
def reset_brand_logo(
    kind: str = "user",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """恢复内置默认 Logo。"""
    if kind not in ("admin", "user"):
        raise HTTPException(400, "Logo 类型仅支持 admin / user")
    for p in _BRAND_DIR.glob(f"{user.id}_{kind}.*"):
        p.unlink(missing_ok=True)
    key = f"brand_{kind}_logo"
    crud.set_settings(db, {key: ""}, user.id)
    settings_service.refresh(db, user.id)
    return {"key": key, "url": ""}


@router.get("/brand")
def get_brand(
    user: User | None = Depends(optional_user), db: Session = Depends(get_db)
):
    """品牌标识：登录用户返回自己的配置，未登录返回站点默认（管理员配置）。

    用户端页面只需读取该接口即可渲染 Logo / 名称 / 副标题。
    """
    uid = user.id if user else crud.get_default_admin_id(db)
    runtime = settings_service.effective(db, uid)
    return _brand_payload(runtime)
