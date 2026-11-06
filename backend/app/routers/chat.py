# -*- coding: utf-8 -*-
"""智能问答接口：SSE 流式输出 + 会话/历史管理（全部按账号隔离）。"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app import crud
from app.models import ChatSession, ChatMessage, User
from app.security import optional_user, require_user
from app.services import category_service, settings_service
from app.services.rag import stream_answer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    question: str
    session_id: str | None = None
    top_k: int | None = None
    visitor_name: str = ""
    module: str = ""
    scenario: str = ""
    visibility: str = "public"


class RenameReq(BaseModel):
    title: str


@router.post("/chat")
async def chat(req: ChatRequest, user: User = Depends(require_user),
               db: Session = Depends(get_db)):
    """管理端问答（source=admin），只能检索当前账号自己的文档。"""
    return _do_chat(req, db, source="admin", user=user)


@router.post("/public/chat")
async def public_chat(req: ChatRequest, user: User | None = Depends(optional_user),
                      db: Session = Depends(get_db)):
    """用户端问答（source=user）。登录后使用自己的知识库，未登录回退到站点默认库。"""
    return _do_chat(req, db, source="user", user=user)


def _do_chat(req: ChatRequest, db: Session, source: str, user: User | None):
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "问题不能为空")

    # 会话归属：登录用户用自己的 id，未登录写空串（站点公共会话）
    uid = user.id if user else ""
    # 检索范围：登录用户自己的文档；匿名访问用管理员（站点默认）的文档
    scope_uid = uid or (crud.get_default_admin_id(db) or "")
    doc_ids = set(crud.list_document_ids(db, scope_uid))

    config = settings_service.get_runtime(uid or None)
    session_id = req.session_id or crud.create_session(
        db, question[:20], source=source, visitor_name=req.visitor_name or "",
        commit=False, user_id=uid,
    )
    # 已存在的会话必须属于当前账号，防止跨账号读写历史
    if req.session_id and not crud.get_session(db, session_id, uid):
        raise HTTPException(403, "会话不存在或不属于当前账号")
    # 会话与用户消息合并为一次提交，少一次数据库往返
    crud.insert_message(db, session_id, "user", question, commit=False)
    db.commit()
    top_k = req.top_k or config["top_k"]

    # 用户端只能检索公开文档；管理端可按所选权限范围检索
    visibility = "public" if source == "user" else (req.visibility or "all")
    filters = {
        "module": req.module.strip(),
        "scenario": req.scenario.strip(),
        "visibility": visibility,
    }

    def event_stream():
        answer_parts: list[str] = []
        sources: list = []
        stream_db = SessionLocal()
        try:
            # 注意：生成器在线程池中迭代，每次 next 的 contextvar 都是新的，
            # 所以账号配置必须通过 runtime 显式传入，不能依赖 set_current_user。
            for event in stream_answer(
                question,
                top_k=top_k,
                threshold=config["score_threshold"],
                temperature=config.get("llm_temperature"),
                web_search_enabled=bool(config.get("web_search_enabled")),
                web_search_mode=config.get("web_search_mode") or "fallback",
                web_max_results=config.get("web_search_max_results"),
                filters=filters,
                doc_ids=doc_ids,
                runtime=config,
            ):
                if event["type"] == "sources":
                    sources = event["sources"]
                elif event["type"] == "token":
                    answer_parts.append(event["content"])
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            # 没有生成任何内容（如未配置 Key 报错）时不落库空回答，避免历史里出现空白气泡
            if answer_parts:
                crud.insert_message(stream_db, session_id, "assistant", "".join(answer_parts), sources)
            yield f"data: {json.dumps({'type': 'session', 'session_id': session_id}, ensure_ascii=False)}\n\n"
        except Exception as e:
            # 兜底：流已开始后出错也要给前端一个 error 事件，避免"发消息后无任何反应"
            logger.exception("问答流异常")
            yield f"data: {json.dumps({'type': 'error', 'message': f'问答服务异常：{e}'}, ensure_ascii=False)}\n\n"
        finally:
            stream_db.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions")
def get_sessions(
    source: str | None = None,
    category: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return crud.list_sessions(db, source=source, category=category, user_id=user.id)


@router.get("/sessions/stats")
def sessions_stats(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """管理端统计：当前账号的会话数、按来源分、按问答分类分、消息数。"""
    scope = ChatSession.user_id == user.id
    total = db.execute(select(func.count(ChatSession.id)).where(scope)).scalar_one()
    user_count = db.execute(
        select(func.count(ChatSession.id)).where(scope, ChatSession.source == "user")
    ).scalar_one()
    admin_count = db.execute(
        select(func.count(ChatSession.id)).where(scope, ChatSession.source == "admin")
    ).scalar_one()
    msg_total = crud.message_count(db, user.id)
    rows = db.execute(
        select(ChatSession.category, func.count(ChatSession.id))
        .where(scope)
        .group_by(ChatSession.category)
    ).all()
    counts = {key or "other": int(n) for key, n in rows}
    return {
        "total_sessions": total,
        "user_sessions": user_count,
        "admin_sessions": admin_count,
        "total_messages": msg_total,
        "category_counts": counts,
        "categories": category_service.list_all(db, user.id),
    }


@router.delete("/sessions/{session_id}")
def remove_session(session_id: str, user: User = Depends(require_user),
                   db: Session = Depends(get_db)):
    crud.delete_session(db, session_id, user.id)
    return {"ok": True}


@router.put("/sessions/{session_id}/rename")
def rename(session_id: str, req: RenameReq, user: User = Depends(require_user),
           db: Session = Depends(get_db)):
    crud.rename_session(db, session_id, req.title, user.id)
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
def get_messages(session_id: str, user: User = Depends(require_user),
                 db: Session = Depends(get_db)):
    # 归属不匹配时明确 404（而不是返回空列表，避免被误判为「该会话没有消息」）
    if not crud.get_session(db, session_id, user.id):
        raise HTTPException(404, "会话不存在或不属于当前账号")
    return crud.list_messages(db, session_id, user.id)
