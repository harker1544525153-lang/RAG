# -*- coding: utf-8 -*-
"""Vercel Serverless 入口：把 backend/app/main.py 的 FastAPI 应用暴露给 Vercel。

Vercel 的 Python runtime 默认把 `api/` 下的文件当作函数入口，
因此这里只做一件事：把 backend/ 加入 sys.path，再导出 `app`。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.main import app  # noqa: E402  （必须在 sys.path 之后导入）
