# -*- coding: utf-8 -*-
"""知识库接口：分类管理 + 分类视图 + 统计总览（全部按当前登录账号隔离）。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app import crud
from app.models import User
from app.security import require_user

router = APIRouter(prefix="/api", tags=["knowledge"])


class CategoryCreate(BaseModel):
    name: str
    description: str = ""
    color: str = "#165dff"
    strategy: str = "standard"
    chunk_size: int = 768
    chunk_overlap: int = 96


class CategoryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = None
    strategy: str | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None


@router.get("/categories")
def get_categories(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return crud.list_categories(db, user.id)


@router.post("/categories")
def add_category(payload: CategoryCreate, user: User = Depends(require_user),
                 db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "分类名称不能为空")
    if crud.get_category_by_name(db, name, user.id):
        raise HTTPException(400, "分类名称已存在")
    cat = crud.create_category(
        db, name, payload.description, payload.color,
        payload.strategy, payload.chunk_size, payload.chunk_overlap,
        user_id=user.id,
    )
    return crud.category_to_dict(cat)


@router.patch("/categories/{category_id}")
def edit_category(category_id: str, payload: CategoryUpdate,
                  user: User = Depends(require_user), db: Session = Depends(get_db)):
    if not crud.get_user_category(db, category_id, user.id):
        raise HTTPException(404, "分类不存在")
    if payload.name:
        existing = crud.get_category_by_name(db, payload.name.strip(), user.id)
        if existing and existing.id != category_id:
            raise HTTPException(400, "分类名称已存在")
    cat = crud.update_category(db, category_id, **payload.model_dump(exclude_unset=True))
    if not cat:
        raise HTTPException(404, "分类不存在")
    return crud.category_to_dict(cat)


@router.delete("/categories/{category_id}")
def remove_category(category_id: str, user: User = Depends(require_user),
                    db: Session = Depends(get_db)):
    if not crud.delete_category(db, category_id, user.id):
        raise HTTPException(404, "分类不存在")
    return {"ok": True}


@router.get("/knowledge/filters")
def knowledge_filters(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """问答检索范围选项：业务模块、适用场景（来自当前账号文档的元数据）。"""
    docs = crud.list_documents(db, user_id=user.id)
    modules = sorted({d["module"] for d in docs if d.get("module")})
    scenarios = sorted({d["scenario"] for d in docs if d.get("scenario")})
    return {
        "modules": modules,
        "scenarios": scenarios,
        "tiers": [
            {"value": "core", "label": "核心权威"},
            {"value": "standard", "label": "普通参考"},
            {"value": "archived", "label": "过期归档"},
        ],
        "strategies": [
            {"value": "standard", "label": "基础标准"},
            {"value": "semantic", "label": "语义分块"},
            {"value": "hierarchical", "label": "层级分块"},
            {"value": "code", "label": "代码结构"},
            {"value": "table", "label": "表格结构"},
            {"value": "flow", "label": "流程结构"},
        ],
    }


@router.get("/knowledge/overview")
def knowledge_overview(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """知识库分类视图：每个分类下挂载当前账号的文档列表 + 统计。"""
    stats = crud.document_stats(db, user.id)
    stats["vector_chunks"] = stats.get("total_chunks", 0)
    stats["sessions"] = crud.session_count(db, user.id)
    stats["messages"] = crud.message_count(db, user.id)

    documents = crud.list_documents(db, user_id=user.id)
    categories = crud.list_categories(db, user.id)
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
