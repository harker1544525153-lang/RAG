# -*- coding: utf-8 -*-
"""智能问答接口：SSE 流式输出 + 会话/历史管理。"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app import crud
from app.models import ChatSession, ChatMessage
from app.services import category_service, settings_service
from app.services.rag import stream_answer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    question: str
    session_id: str | None = None
    top_k: int | None = None
    visitor_name: str = ""


class RenameReq(BaseModel):
    title: str


@router.post("/chat")
async def chat(req: ChatRequest, db: Session = Depends(get_db)):
    """管理端问答（默认 source=admin）。"""
    return _do_chat(req, db, source="admin")


@router.post("/public/chat")
async def public_chat(req: ChatRequest, db: Session = Depends(get_db)):
    """用户端公开问答（默认 source=user，visitor_name 可选）。"""
    return _do_chat(req, db, source="user")


def _do_chat(req: ChatRequest, db: Session, source: str):
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "问题不能为空")

    config = settings_service.get_runtime()
    session_id = req.session_id or crud.create_session(
        db, question[:20], source=source, visitor_name=req.visitor_name or "", commit=False
    )
    # 会话与用户消息合并为一次提交，少一次数据库往返
    crud.insert_message(db, session_id, "user", question, commit=False)
    db.commit()
    top_k = req.top_k or config["top_k"]

    def event_stream():
        answer_parts: list[str] = []
        sources: list = []
        stream_db = SessionLocal()
        try:
            for event in stream_answer(
                question,
                top_k=top_k,
                threshold=config["score_threshold"],
                temperature=config.get("llm_temperature"),
                web_search_enabled=bool(config.get("web_search_enabled")),
                web_search_mode=config.get("web_search_mode") or "fallback",
                web_max_results=config.get("web_search_max_results"),
            ):
                if event["type"] == "sources":
                    sources = event["sources"]
                elif event["type"] == "token":
                    answer_parts.append(event["content"])
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            crud.insert_message(stream_db, session_id, "assistant", "".join(answer_parts), sources)
            yield f"data: {json.dumps({'type': 'session', 'session_id': session_id}, ensure_ascii=False)}\n\n"
        finally:
            stream_db.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions")
def get_sessions(source: str | None = None, category: str | None = None,
                 db: Session = Depends(get_db)):
    return crud.list_sessions(db, source=source, category=category)


@router.get("/sessions/stats")
def sessions_stats(db: Session = Depends(get_db)):
    """管理端统计：总会话数、按来源分、按问答分类分、总消息数。"""
    total = db.query(ChatSession).count()
    user_count = db.query(ChatSession).filter(ChatSession.source == "user").count()
    admin_count = db.query(ChatSession).filter(ChatSession.source == "admin").count()
    msg_total = db.query(ChatMessage).count()
    rows = db.execute(
        select(ChatSession.category, func.count(ChatSession.id))
        .group_by(ChatSession.category)
    ).all()
    counts = {key or "other": int(n) for key, n in rows}
    return {
        "total_sessions": total,
        "user_sessions": user_count,
        "admin_sessions": admin_count,
        "total_messages": msg_total,
        "category_counts": counts,
        "categories": category_service.list_all(db),
    }


@router.delete("/sessions/{session_id}")
def remove_session(session_id: str, db: Session = Depends(get_db)):
    crud.delete_session(db, session_id)
    return {"ok": True}


@router.put("/sessions/{session_id}/rename")
def rename(session_id: str, req: RenameReq, db: Session = Depends(get_db)):
    crud.rename_session(db, session_id, req.title)
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
def get_messages(session_id: str, db: Session = Depends(get_db)):
    return crud.list_messages(db, session_id)
