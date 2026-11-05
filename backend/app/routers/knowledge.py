# -*- coding: utf-8 -*-
"""知识库接口：分类管理 + 分类视图 + 统计总览。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app import crud
from app.services.vector import get_vector_store

router = APIRouter(prefix="/api", tags=["knowledge"])


class CategoryCreate(BaseModel):
    name: str
    description: str = ""
    color: str = "#165dff"


class CategoryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = None


@router.get("/categories")
def get_categories(db: Session = Depends(get_db)):
    return crud.list_categories(db)


@router.post("/categories")
def add_category(payload: CategoryCreate, db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "分类名称不能为空")
    if crud.get_category_by_name(db, name):
        raise HTTPException(400, "分类名称已存在")
    cat = crud.create_category(db, name, payload.description, payload.color)
    return crud.category_to_dict(cat)


@router.patch("/categories/{category_id}")
def edit_category(category_id: str, payload: CategoryUpdate, db: Session = Depends(get_db)):
    if payload.name:
        existing = crud.get_category_by_name(db, payload.name.strip())
        if existing and existing.id != category_id:
            raise HTTPException(400, "分类名称已存在")
    cat = crud.update_category(db, category_id, **payload.model_dump(exclude_unset=True))
    if not cat:
        raise HTTPException(404, "分类不存在")
    return crud.category_to_dict(cat)


@router.delete("/categories/{category_id}")
def remove_category(category_id: str, db: Session = Depends(get_db)):
    if not crud.delete_category(db, category_id):
        raise HTTPException(404, "分类不存在")
    return {"ok": True}


@router.get("/knowledge/overview")
def knowledge_overview(db: Session = Depends(get_db)):
    """知识库分类视图：每个分类下挂载文档列表 + 全局统计。"""
    stats = crud.document_stats(db)
    stats["vector_chunks"] = get_vector_store().count()
    stats["sessions"] = crud.session_count(db)
    stats["messages"] = crud.message_count(db)

    documents = crud.list_documents(db)
    categories = crud.list_categories(db)
    grouped: dict[str, list] = {c["id"]: [] for c in categories}
    uncategorized: list = []
    for doc in documents:
        if doc["category_id"] and doc["category_id"] in grouped:
            grouped[doc["category_id"]].append(doc)
        else:
            uncategorized.append(doc)

    for cat in categories:
        cat["documents"] = grouped[cat["id"]]
        cat["chunk_total"] = sum(d["chunk_count"] or 0 for d in cat["documents"])
        cat["size_total"] = sum(d["file_size"] or 0 for d in cat["documents"])

    return {
        "stats": stats,
        "categories": categories,
        "uncategorized": {
            "name": "未分类",
            "color": "#86909c",
            "documents": uncategorized,
            "chunk_total": sum(d["chunk_count"] or 0 for d in uncategorized),
            "size_total": sum(d["file_size"] or 0 for d in uncategorized),
        },
    }
