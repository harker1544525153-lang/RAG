# -*- coding: utf-8 -*-
"""问答归档分类管理接口（管理端「设置」模块使用）。

分类标识 id 一旦创建不建议修改（历史会话按 id 归档）；
删除分类时会把已有会话回落到「其他」。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
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
def list_categories(db: Session = Depends(get_db)):
    return category_service.list_all(db)


@router.post("")
def create_category(payload: QaCategoryIn, db: Session = Depends(get_db)):
    try:
        return category_service.create(db, payload.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/{cid}")
def update_category(cid: str, payload: QaCategoryUpdate, db: Session = Depends(get_db)):
    try:
        return category_service.update(db, cid, payload.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{cid}")
def delete_category(cid: str, db: Session = Depends(get_db)):
    try:
        category_service.delete(db, cid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"deleted": cid}
