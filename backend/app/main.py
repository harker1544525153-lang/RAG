# -*- coding: utf-8 -*-
"""FastAPI 入口：路由挂载 + CORS + 启动初始化（建表/默认分类/初始文档导入）+ 前端静态托管。"""
import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func

from app.config import BASE_DIR, settings
from app.db import SessionLocal, init_db
from app.models import ChatMessage, Document
from app import crud
from app.routers import chat, documents, knowledge, qa_categories, settings as settings_router
from app.services import category_service, settings_service
from app.services.seed import seed_documents
from app.services.embeddings import warmup as warmup_embeddings
from app.services.vector import get_vector_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Vercel / Serverless 环境标记：跳过常驻后台线程（会被冻结，且拖慢冷启动）
_IS_SERVERLESS = bool(os.getenv("VERCEL"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        crud.ensure_default_categories(db)
        category_service.ensure_seed(db)
        if not _IS_SERVERLESS:
            # 历史数据维护只在常驻部署执行：Serverless 每次冷启动都跑一遍会明显拖慢首个请求
            crud.clear_stale_processing(db)
            backfilled = crud.backfill_session_categories(db)
            if backfilled:
                logger.info("历史会话自动归档完成：%d 条", backfilled)
        # 载入管理端保存的运行时配置（DB 覆盖 .env）
        settings_service.refresh(db)
        logger.info("运行时配置: 大模型=%s 向量化=%s", 
                    settings_service.get_runtime()["llm_model"],
                    settings_service.get_runtime()["embedding_provider"])
    finally:
        db.close()

    if settings.SEED_ON_STARTUP and not _IS_SERVERLESS:
        # 后台导入，避免大文档向量化阻塞服务启动
        threading.Thread(target=seed_documents, name="seed-docs", daemon=True).start()

    # 后台预热向量化模型：避免用户首次提问时等待冷启动（CPU 下约 20 秒）
    # Serverless 中后台线程会被冻结，跳过
    if not _IS_SERVERLESS and settings_service.get_runtime().get("embedding_provider", "").lower() != "hash":
        threading.Thread(target=warmup_embeddings, name="warmup-embed", daemon=True).start()

    logger.info("服务启动: %s", settings)
    yield
    logger.info("服务关闭")


app = FastAPI(title="企业级 RAG 智能文档问答系统", version="2.0.0", lifespan=lifespan)

_origins = os.getenv("ALLOW_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins.split(",")] if _origins != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(documents.view_router)
app.include_router(chat.router)
app.include_router(knowledge.router)
app.include_router(settings_router.router)
app.include_router(qa_categories.router)


@app.get("/api/health")
def health():
    db = SessionLocal()
    try:
        doc_total = db.execute(select(func.count(Document.id))).scalar_one()
        msg_total = db.execute(select(func.count(ChatMessage.id))).scalar_one()
    finally:
        db.close()
    return {
        "status": "ok",
        "database": "postgresql" if settings.is_postgres else "sqlite",
        "vector_backend": settings.effective_vector_backend,
        "embedding_provider": settings_service.get_runtime().get("embedding_provider"),
        "documents": doc_total,
        "chunks": get_vector_store().count(),
        "messages": msg_total,
    }


# 前端静态托管
#   /admin/*  → frontend/dist/  （后台管理系统）
#   /*        → user/          （用户端智能客服）
_admin_dist = BASE_DIR.parent / "frontend" / "dist"
_user_dir = BASE_DIR.parent / "user"

if _admin_dist.exists():
    app.mount("/admin/assets", StaticFiles(directory=_admin_dist / "assets"), name="admin-assets")

    @app.get("/admin/{full_path:path}")
    def serve_admin(full_path: str):
        target = _admin_dist / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(_admin_dist / "index.html")

if _user_dir.exists():
    @app.get("/")
    def serve_user_root():
        return FileResponse(_user_dir / "index.html")

    @app.get("/user/{full_path:path}")
    def serve_user_assets(full_path: str):
        target = _user_dir / full_path
        if target.is_file():
            return FileResponse(target)
        return FileResponse(_user_dir / "index.html")


if __name__ == "__main__":
    import socket
    import sys

    import uvicorn

    # 启动前先探测端口，避免抛出难懂的 WinError 10048 堆栈
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _probe:
        try:
            _probe.bind((settings.HOST, settings.PORT))
        except OSError:
            logger.error(
                "端口 %s 已被占用，无法启动。\n"
                "  1) 最常见原因：上一次启动的后端还在运行 —— 回到它的终端按 Ctrl+C\n"
                "  2) 若找不到旧终端，在 PowerShell 中执行：\n"
                "       Get-NetTCPConnection -LocalPort %s -State Listen | Select-Object OwningProcess\n"
                "       Stop-Process -Id <上面的PID> -Force\n"
                "  3) 也可以改 backend/.env 里的 PORT 换一个端口（同时同步 frontend/vite.config.js 的代理）",
                settings.PORT, settings.PORT,
            )
            sys.exit(1)

    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
