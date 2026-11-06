# -*- coding: utf-8 -*-
"""问答归档分类管理接口（管理端「设置」模块使用）。

分类标识 id 一旦创建不建议修改（历史会话按 id 归档）；
删除分类时会把已有会话回落到「其他」。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import require_user
from app.services import category_service

router = APIRouter(prefix="/api/qa-categories", tags=["settings"])


class QaCategoryIn(BaseModel):
    id: str | None = None
    label: str
    color: str = "#165dff"
    keywords: str = ""
    sort_order: int = 0
    enabled: bool = True


class QaCategoryUpdate(BaseModel):
    label: str | None = None
    color: str | None = None
    keywords: str | None = None
    sort_order: int | None = None
    enabled: bool | None = None


@router.get("")
def list_categories(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return category_service.list_all(db, user.id)


@router.post("")
def create_category(payload: QaCategoryIn, user: User = Depends(require_user),
                    db: Session = Depends(get_db)):
    try:
        return category_service.create(db, payload.model_dump(), user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/{cid}")
def update_category(cid: str, payload: QaCategoryUpdate, user: User = Depends(require_user),
                    db: Session = Depends(get_db)):
    try:
        return category_service.update(db, cid, payload.model_dump(exclude_none=True), user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{cid}")
def delete_category(cid: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    try:
        category_service.delete(db, cid, user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"deleted": cid}
