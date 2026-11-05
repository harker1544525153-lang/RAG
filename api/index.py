# -*- coding: utf-8 -*-
"""Vercel Serverless 入口：把 backend/app/main.py 的 FastAPI 应用暴露给 Vercel。

Vercel 的 Python runtime 默认把 `api/` 下的文件当作函数入口，
**必须存在模块顶层的 `app` / `application` / `handler`**，因此下面固定写
`app = _build_app()`，不能把赋值放进 try/except（Vercel 静态检测会判定找不到入口）。

若导入阶段崩溃（依赖缺失、includeFiles 未打包 backend 等），
会退化为一个零依赖的 ASGI 响应，直接返回启动错误，便于线上定位。
"""
import json
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def _mask(text: str) -> str:
    """脱敏：隐藏连接串中的账号密码。"""
    return re.sub(r"://[^\s/@]*:[^\s/@]*@", "://***:***@", text)


def _boot_failed_app(message: str):
    """fastapi 都不可用时的兜底 ASGI 应用，只返回诊断信息。"""
    payload = json.dumps(
        {"status": "boot_failed", "error": message}, ensure_ascii=False
    ).encode("utf-8")

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        await send({
            "type": "http.response.start",
            "status": 500,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(payload)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": payload})

    return app


def _build_app():
    """导入真实 FastAPI 应用；失败时返回可读诊断信息的兜底应用。"""
    try:
        from app.main import app as fastapi_app

        return fastapi_app
    except Exception:
        detail = _mask(traceback.format_exc())
        checks = {
            "root": str(ROOT),
            "backend_app_exists": (ROOT / "backend" / "app" / "main.py").is_file(),
            "frontend_dist_exists": (ROOT / "frontend" / "dist" / "index.html").is_file(),
            "user_exists": (ROOT / "user" / "index.html").is_file(),
        }
        message = json.dumps(
            {"checks": checks, "traceback": detail[-4000:]}, ensure_ascii=False
        )
        return _boot_failed_app(message)


app = _build_app()
