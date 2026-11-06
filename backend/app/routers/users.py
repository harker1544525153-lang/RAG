# -*- coding: utf-8 -*-
"""管理员功能：用户管理与对话统计（仅 role=admin 可访问）。"""
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app import crud
from app.models import ChatMessage, ChatSession, User, UserLoginLog
from app.security import create_widget_token, hash_password, require_admin, user_to_dict

router = APIRouter(tags=["admin"])


class UserUpdate(BaseModel):
    nickname: str | None = None
    role: str | None = None
    disabled: bool | None = None


class ResetPasswordPayload(BaseModel):
    password: str = Field(min_length=6, max_length=32)


# ---------------- 用户管理 ----------------
@router.get("/api/users")
def list_users(
    keyword: str = "",
    role: str = "",
    disabled: str = "",
    page: int = 1,
    page_size: int = 20,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    stmt = select(User)
    if keyword.strip():
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(or_(User.phone.like(like), User.nickname.like(like)))
    if role:
        stmt = stmt.where(User.role == role)
    if disabled in ("0", "1"):
        stmt = stmt.where(User.disabled == int(disabled))

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    rows = db.execute(
        stmt.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return {"items": [user_to_dict(u) for u in rows], "total": total,
            "page": page, "page_size": page_size}


def _get_user_or_404(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@router.get("/api/users/{user_id}")
def get_user(user_id: str, _admin: User = Depends(require_admin),
             db: Session = Depends(get_db)):
    user = _get_user_or_404(db, user_id)
    data = user_to_dict(user)
    data["login_logs"] = _login_logs(db, user)
    return data


def _login_logs(db: Session, user: User, limit: int = 50) -> list[dict]:
    rows = db.execute(
        select(UserLoginLog)
        .where(UserLoginLog.user_id == user.id)
        .order_by(UserLoginLog.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return [{
        "id": r.id,
        "success": bool(r.success),
        "reason": r.reason,
        "ip": r.ip,
        "user_agent": r.user_agent,
        "created_at": r.created_at.isoformat(sep=" ", timespec="seconds"),
    } for r in rows]


@router.patch("/api/users/{user_id}")
def update_user(user_id: str, payload: UserUpdate,
                admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _get_user_or_404(db, user_id)
    if user.id == admin.id and (payload.disabled or (payload.role and payload.role != "admin")):
        raise HTTPException(status_code=400, detail="不能禁用或降级当前登录的管理员账号")
    if payload.role is not None:
        if payload.role not in ("user", "admin"):
            raise HTTPException(status_code=400, detail="角色只能是 user 或 admin")
        if user.role == "admin" and payload.role != "admin":
            admin_count = db.execute(
                select(func.count(User.id)).where(User.role == "admin", User.disabled == 0)
            ).scalar_one()
            if admin_count <= 1:
                raise HTTPException(status_code=400, detail="系统至少保留一个管理员")
        user.role = payload.role
    if payload.disabled is not None:
        user.disabled = 1 if payload.disabled else 0
    if payload.nickname is not None:
        user.nickname = payload.nickname.strip()[:60]
    db.commit()
    db.refresh(user)
    return user_to_dict(user)


@router.get("/api/users/{user_id}/widget-link")
def user_widget_link(user_id: str, _admin: User = Depends(require_admin),
                     db: Session = Depends(get_db)):
    """管理员为任意账号生成用户端问答链接（免登录，按该账号配置与文档回答）。"""
    target = _get_user_or_404(db, user_id)
    token = create_widget_token(target.id)
    return {"user_id": target.id, "token": token, "url": f"/user/index.html?at={token}"}


@router.post("/api/users/{user_id}/reset-password")
def reset_password(user_id: str, payload: ResetPasswordPayload,
                   _admin: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    user = _get_user_or_404(db, user_id)
    user.password_hash = hash_password(payload.password)
    db.commit()
    return {"ok": True, "message": "密码已重置"}


@router.delete("/api/users/{user_id}")
def delete_user(user_id: str, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    user = _get_user_or_404(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除当前登录的管理员账号")
    if user.role == "admin":
        admin_count = db.execute(
            select(func.count(User.id)).where(User.role == "admin", User.disabled == 0)
        ).scalar_one()
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="系统至少保留一个管理员")
    db.delete(user)
    db.commit()
    # 账号数据隔离：删除账号时一并清理其文档、切片、分类、会话与设置
    cleaned = crud.delete_user_data(db, user_id)
    return {
        "ok": True,
        "message": f"账号及其专属数据已删除（清理文档 {cleaned['documents']} 篇）",
    }


# ---------------- 对话统计 ----------------
@router.get("/api/stats/chat")
def chat_stats(days: int = 7, _admin: User = Depends(require_admin),
               db: Session = Depends(get_db)):
    days = min(90, max(1, days))
    since = datetime.now() - timedelta(days=days - 1)
    since_day = since.replace(hour=0, minute=0, second=0, microsecond=0)

    def count(model, *conds) -> int:
        stmt = select(func.count(model.id))
        for c in conds:
            stmt = stmt.where(c)
        return db.execute(stmt).scalar_one()

    sessions_total = count(ChatSession)
    user_sessions = count(ChatSession, ChatSession.source == "user")
    admin_sessions = count(ChatSession, ChatSession.source == "admin")
    messages_total = count(ChatMessage)
    questions_total = count(ChatMessage, ChatMessage.role == "user")
    users_total = count(User)
    active_users = db.execute(
        select(func.count(func.distinct(ChatSession.visitor_name)))
        .where(ChatSession.created_at >= since_day, ChatSession.visitor_name != "")
    ).scalar_one()

    # 近 N 天趋势：按日统计会话数与提问数（SQLite / PostgreSQL 通用）
    day_col = func.date(ChatSession.created_at)
    srows = db.execute(
        select(day_col, func.count(ChatSession.id))
        .where(ChatSession.created_at >= since_day)
        .group_by(day_col)
    ).all()
    session_by_day = {str(r[0])[:10]: int(r[1]) for r in srows}
    mrows = db.execute(
        select(func.date(ChatMessage.created_at), func.count(ChatMessage.id))
        .where(ChatMessage.created_at >= since_day, ChatMessage.role == "user")
        .group_by(func.date(ChatMessage.created_at))
    ).all()
    question_by_day = {str(r[0])[:10]: int(r[1]) for r in mrows}
    trend = []
    for i in range(days):
        day = (since_day + timedelta(days=i)).strftime("%Y-%m-%d")
        trend.append({
            "date": day,
            "sessions": session_by_day.get(day, 0),
            "questions": question_by_day.get(day, 0),
        })

    by_source = [
        {"source": r[0] or "unknown", "count": int(r[1])}
        for r in db.execute(
            select(ChatSession.source, func.count(ChatSession.id))
            .group_by(ChatSession.source)
        ).all()
    ]
    by_category = [
        {"category": r[0] or "", "count": int(r[1])}
        for r in db.execute(
            select(ChatSession.category, func.count(ChatSession.id))
            .where(ChatSession.category != "")
            .group_by(ChatSession.category)
            .order_by(func.count(ChatSession.id).desc())
            .limit(10)
        ).all()
    ]
    top_questions = [
        {"question": r[0], "count": int(r[1])}
        for r in db.execute(
            select(ChatMessage.content, func.count(ChatMessage.id))
            .where(ChatMessage.role == "user", ChatMessage.created_at >= since_day)
            .group_by(ChatMessage.content)
            .order_by(func.count(ChatMessage.id).desc())
            .limit(8)
        ).all()
        if r[0] and len(r[0]) <= 80
    ]

    return {
        "days": days,
        "totals": {
            "sessions": sessions_total,
            "user_sessions": user_sessions,
            "admin_sessions": admin_sessions,
            "messages": messages_total,
            "questions": questions_total,
            "users": users_total,
            "active_users": active_users,
        },
        "trend": trend,
        "by_source": by_source,
        "by_category": by_category,
        "top_questions": top_questions,
    }
