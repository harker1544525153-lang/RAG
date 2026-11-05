# -*- coding: utf-8 -*-
"""数据访问层：文档 / 分类 / 会话 / 消息 / 运行时设置。"""
import json
import uuid
from datetime import datetime

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import AppSetting, Category, ChatMessage, ChatSession, Document
from app.services import category_service


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def fmt_dt(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


# ---------------- 分类 ----------------

DEFAULT_CATEGORIES = [
    {"name": "App 用户端", "description": "App 端产品与功能文档", "color": "#165dff"},
    {"name": "后台管理", "description": "后台管理系统相关文档", "color": "#00b42a"},
    {"name": "通用文档", "description": "其他知识库资料", "color": "#ff7d00"},
]


def category_to_dict(c: Category, doc_count: int = 0) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "description": c.description or "",
        "color": c.color,
        "doc_count": doc_count,
        "created_at": fmt_dt(c.created_at),
    }


def list_categories(db) -> list[dict]:
    rows = db.execute(select(Category).order_by(Category.created_at)).scalars().all()
    counts = dict(
        db.execute(
            select(Document.category_id, func.count(Document.id))
            .group_by(Document.category_id)
        ).all()
    )
    return [category_to_dict(c, counts.get(c.id, 0)) for c in rows]


def get_category(db, category_id: str) -> Category | None:
    return db.get(Category, category_id)


def get_category_by_name(db, name: str) -> Category | None:
    return db.execute(select(Category).where(Category.name == name)).scalar_one_or_none()


def create_category(db, name: str, description: str = "", color: str = "#165dff") -> Category:
    cat = Category(id=new_id(), name=name.strip(), description=description, color=color)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def get_or_create_category(db, name: str, description: str = "", color: str = "#165dff") -> Category:
    cat = get_category_by_name(db, name)
    return cat or create_category(db, name, description, color)


def update_category(db, category_id: str, **fields) -> Category | None:
    cat = db.get(Category, category_id)
    if not cat:
        return None
    for key in ("name", "description", "color"):
        if fields.get(key) is not None:
            setattr(cat, key, fields[key].strip() if isinstance(fields[key], str) else fields[key])
    db.commit()
    db.refresh(cat)
    return cat


def delete_category(db, category_id: str) -> bool:
    cat = db.get(Category, category_id)
    if not cat:
        return False
    db.execute(
        Document.__table__.update().where(Document.category_id == category_id).values(category_id=None)
    )
    db.delete(cat)
    db.commit()
    return True


def ensure_default_categories(db) -> None:
    if db.execute(select(func.count(Category.id))).scalar_one() == 0:
        for item in DEFAULT_CATEGORIES:
            db.add(Category(id=new_id(), **item))
        db.commit()


# ---------------- 文档 ----------------

def document_to_dict(d: Document) -> dict:
    return {
        "id": d.id,
        "filename": d.filename,
        "file_type": d.file_type,
        "file_size": d.file_size,
        "chunk_count": d.chunk_count,
        "status": d.status,
        "error": d.error,
        "category_id": d.category_id,
        "category_name": d.category.name if d.category else None,
        "category_color": d.category.color if d.category else None,
        "source": d.source,
        "created_at": fmt_dt(d.created_at),
        "updated_at": fmt_dt(d.updated_at),
    }


def insert_document(
    db,
    *,
    filename: str,
    file_type: str,
    file_size: int,
    category_id: str | None = None,
    source: str = "upload",
    file_hash: str | None = None,
) -> Document:
    doc = Document(
        id=new_id(),
        filename=filename,
        file_type=file_type,
        file_size=file_size,
        status="processing",
        category_id=category_id,
        source=source,
        file_hash=file_hash,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def update_document(db, doc_id: str, **fields) -> Document | None:
    doc = db.get(Document, doc_id)
    if not doc:
        return None
    for key, value in fields.items():
        if hasattr(doc, key) and value is not None:
            setattr(doc, key, value)
    db.commit()
    db.refresh(doc)
    return doc


def list_documents(
    db,
    *,
    category_id: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
) -> list[dict]:
    stmt = select(Document)
    if category_id:
        stmt = stmt.where(Document.category_id == category_id)
    if status:
        stmt = stmt.where(Document.status == status)
    if keyword:
        stmt = stmt.where(Document.filename.ilike(f"%{keyword}%"))
    stmt = stmt.order_by(Document.created_at.desc())
    return [document_to_dict(d) for d in db.execute(stmt).scalars().all()]


def get_document(db, doc_id: str) -> Document | None:
    return db.get(Document, doc_id)


def find_document_by_hash(db, file_hash: str) -> Document | None:
    return db.execute(select(Document).where(Document.file_hash == file_hash)).scalar_one_or_none()


def delete_document(db, doc_id: str) -> None:
    doc = db.get(Document, doc_id)
    if doc:
        db.delete(doc)
        db.commit()


def document_stats(db) -> dict:
    total = db.execute(select(func.count(Document.id))).scalar_one()
    total_chunks = db.execute(select(func.coalesce(func.sum(Document.chunk_count), 0))).scalar_one()
    total_size = db.execute(select(func.coalesce(func.sum(Document.file_size), 0))).scalar_one()
    ready = db.execute(select(func.count(Document.id)).where(Document.status == "ready")).scalar_one()
    processing = db.execute(
        select(func.count(Document.id)).where(Document.status == "processing")
    ).scalar_one()
    failed = db.execute(select(func.count(Document.id)).where(Document.status == "failed")).scalar_one()

    by_type = [
        {"type": t, "count": c}
        for t, c in db.execute(
            select(Document.file_type, func.count(Document.id)).order_by(func.count(Document.id).desc())
        ).all()
    ]
    by_category = [
        {"category_id": cid, "name": name or "未分类", "color": color or "#86909c", "count": c}
        for cid, name, color, c in db.execute(
            select(Category.id, Category.name, Category.color, func.count(Document.id))
            .select_from(Document)
            .outerjoin(Category, Document.category_id == Category.id)
            .group_by(Category.id, Category.name, Category.color)
        ).all()
    ]
    return {
        "total_documents": total,
        "total_chunks": int(total_chunks or 0),
        "total_size": int(total_size or 0),
        "status": {"ready": ready, "processing": processing, "failed": failed},
        "by_type": by_type,
        "by_category": by_category,
    }


# ---------------- 会话与消息 ----------------

def create_session(db, title: str = "新会话", source: str = "admin", visitor_name: str = "",
                   category: str | None = None, commit: bool = True) -> str:
    sid = new_id()
    # 一个用户问题自动归入一个分类（分类与关键词在管理端「设置」维护）
    cate = category if category is not None else category_service.classify(db, title)
    db.add(
        ChatSession(
            id=sid,
            title=title or "新会话",
            source=source,
            visitor_name=visitor_name,
            category=cate,
        )
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return sid


def list_sessions(db, source: str | None = None, category: str | None = None,
                  limit: int = 200) -> list[dict]:
    stmt = select(ChatSession).order_by(ChatSession.created_at.desc()).limit(limit)
    if source:
        stmt = stmt.where(ChatSession.source == source)
    if category:
        stmt = stmt.where(ChatSession.category == category)
    rows = db.execute(stmt).scalars().all()
    meta = category_service.meta(db)
    result = []
    for s in rows:
        msg_count = db.execute(
            select(func.count(ChatMessage.id)).where(ChatMessage.session_id == s.id)
        ).scalar_one()
        cate = getattr(s, "category", "") or "other"
        info = meta.get(cate, {"label": "其他", "color": "#8a94a6"})
        result.append(
            {
                "id": s.id,
                "title": s.title,
                "source": getattr(s, "source", "admin"),
                "visitor_name": getattr(s, "visitor_name", ""),
                "category": cate,
                "category_label": info["label"],
                "category_color": info["color"],
                "msg_count": msg_count,
                "created_at": fmt_dt(s.created_at),
            }
        )
    return result


def backfill_session_categories(db) -> int:
    """给旧版本创建的会话补齐分类（只处理 empty/NULL，不覆盖已归档结果）。"""
    rows = db.execute(
        select(ChatSession).where(
            (ChatSession.category.is_(None)) | (ChatSession.category == "")
        )
    ).scalars().all()
    for s in rows:
        s.category = category_service.classify(db, s.title)
    if rows:
        db.commit()
    return len(rows)


def delete_session(db, session_id: str) -> None:
    session = db.get(ChatSession, session_id)
    if session:
        db.delete(session)  # relationship cascade 自动删除消息
        db.commit()


def rename_session(db, session_id: str, title: str) -> None:
    session = db.get(ChatSession, session_id)
    if session:
        session.title = title
        db.commit()


def insert_message(db, session_id: str, role: str, content: str,
                   sources: list | None = None, commit: bool = True) -> str:
    msg_id = new_id()
    db.add(
        ChatMessage(
            id=msg_id,
            session_id=session_id,
            role=role,
            content=content,
            sources=json.dumps(sources, ensure_ascii=False) if sources else None,
        )
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return msg_id


def list_messages(db, session_id: str) -> list[dict]:
    rows = db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at, ChatMessage.id)
    ).scalars().all()
    return [
        {
            "id": m.id,
            "session_id": m.session_id,
            "role": m.role,
            "content": m.content,
            "sources": json.loads(m.sources) if m.sources else [],
            "created_at": fmt_dt(m.created_at),
        }
        for m in rows
    ]


# ---------------- 运行时设置 ----------------

def get_all_settings(db) -> dict[str, str]:
    return {s.key: s.value for s in db.execute(select(AppSetting)).scalars().all()}


def set_settings(db, values: dict[str, str]) -> None:
    for key, value in values.items():
        row = db.get(AppSetting, key)
        if row:
            row.value = str(value)
        else:
            db.add(AppSetting(key=key, value=str(value)))
    db.commit()


def clear_stale_processing(db) -> None:
    """服务异常退出后，重启时把残留的 processing 文档标记为 failed。"""
    stale = db.execute(select(Document).where(Document.status == "processing")).scalars().all()
    for d in stale:
        d.status = "failed"
        d.error = "服务重启，处理中断"
    if stale:
        db.commit()


def session_count(db) -> int:
    return db.execute(select(func.count(ChatSession.id))).scalar_one()


def message_count(db) -> int:
    return db.execute(select(func.count(ChatMessage.id))).scalar_one()
