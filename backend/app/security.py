# -*- coding: utf-8 -*-
"""账号安全：密码哈希、登录令牌、图形验证码与请求鉴权依赖（零额外依赖）。"""
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from threading import Lock
from xml.sax.saxutils import escape

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User

logger = logging.getLogger(__name__)

# ---------------- 密码：PBKDF2-SHA256 ----------------
_ITERATIONS = 120_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except (ValueError, AttributeError):
        return False
    calc = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS).hex()
    return hmac.compare_digest(calc, digest)


# ---------------- 登录令牌：无状态 HMAC ----------------
_secret_cache: bytes | None = None


def _secret() -> bytes:
    global _secret_cache
    if _secret_cache:
        return _secret_cache
    raw = (settings.AUTH_SECRET or "").strip()
    if raw:
        _secret_cache = raw.encode()
        return _secret_cache
    # 未配置时自动生成并落盘，避免重启后所有登录态失效
    path = settings.DATA_DIR / ".auth_secret"
    try:
        if path.exists():
            _secret_cache = path.read_bytes().strip()
        else:
            token = secrets.token_urlsafe(48).encode()
            path.write_bytes(token)
            _secret_cache = token
    except OSError:
        _secret_cache = b"gala-rag-fallback-secret"
    return _secret_cache


def _sign(payload: dict) -> str:
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode()
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def create_token(user_id: str) -> str:
    return _sign({
        "uid": user_id,
        "exp": int(time.time()) + settings.TOKEN_TTL_DAYS * 86400,
        "n": secrets.token_hex(4),
    })


# 用户端问答分享令牌：长期有效，只用于该账号的用户端问答与只读配置，
# 不具备任何后台管理权限（require_user 会拒绝 scope=widget 的令牌）。
WIDGET_TTL_SECONDS = 3650 * 86400


def create_widget_token(user_id: str) -> str:
    return _sign({
        "uid": user_id,
        "exp": int(time.time()) + WIDGET_TTL_SECONDS,
        "n": secrets.token_hex(4),
        "scope": "widget",
    })


def parse_token(token: str) -> dict | None:
    if not token:
        return None
    try:
        body, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expect = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body.encode()).decode())
    except (ValueError, TypeError):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


# ---------------- 图形验证码：SVG + 内存缓存 ----------------
_CAPTCHA_TTL = 300
_captchas: dict[str, tuple[str, float]] = {}
_captcha_lock = Lock()
_CAPTCHA_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def create_captcha() -> tuple[str, str]:
    """返回 (captcha_id, svg_text)。5 分钟有效且一次性。"""
    code = "".join(secrets.choice(_CAPTCHA_CHARS) for _ in range(4))
    cid = uuid.uuid4().hex
    with _captcha_lock:
        now = time.time()
        for k, (_, ts) in list(_captchas.items()):
            if ts < now:
                _captchas.pop(k, None)
        _captchas[cid] = (code, now + _CAPTCHA_TTL)
    return cid, _captcha_svg(code)


def _captcha_svg(code: str) -> str:
    width, height = 140, 44
    rnd = secrets.SystemRandom()
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f5f8ff"/>',
    ]
    for _ in range(3):
        x1, y1 = rnd.randint(0, width), rnd.randint(0, height)
        x2, y2 = rnd.randint(0, width), rnd.randint(0, height)
        color = "#%02x%02x%02x" % (
            rnd.randint(120, 190), rnd.randint(150, 200), rnd.randint(190, 235),
        )
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="1.4"/>'
        )
    for i, ch in enumerate(code):
        x = 13 + i * 31 + rnd.randint(-2, 2)
        y = rnd.randint(27, 35)
        rot = rnd.randint(-18, 18)
        color = "#%02x%02x%02x" % (
            rnd.randint(20, 90), rnd.randint(60, 120), rnd.randint(140, 220),
        )
        parts.append(
            f'<text x="{x}" y="{y}" font-size="{rnd.randint(24, 28)}" '
            f'font-family="Arial,Helvetica,sans-serif" font-weight="700" fill="{color}" '
            f'text-anchor="middle" transform="rotate({rot} {x} {y})">{escape(ch)}</text>'
        )
    for _ in range(14):
        cx, cy = rnd.randint(0, width), rnd.randint(0, height)
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{rnd.randint(1, 2)}" fill="#c8d6ea"/>')
    parts.append("</svg>")
    return "".join(parts)


def check_captcha(captcha_id: str, code: str) -> bool:
    """校验并立即作废（防重复使用）。"""
    with _captcha_lock:
        item = _captchas.pop((captcha_id or "").strip(), None)
    if not item or item[1] < time.time():
        return False
    return (code or "").strip().upper() == item[0].upper()


# ---------------- 请求鉴权依赖 ----------------
def _extract_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.query_params.get("token", "")


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """未携带令牌 → 匿名（None）；主动携带令牌但无效 → 401。

    不能把「带了 token 但已失效」当作匿名处理，否则会悄悄落到站点默认库
    （管理员文档），造成跨账号数据泄露。前端收到 401 后应清除登录态并重新登录。
    """
    token = _extract_token(request).strip()
    if not token:
        return None
    payload = parse_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="登录状态已失效，请重新登录")
    user = db.get(User, payload.get("uid"))
    if not user or user.disabled:
        raise HTTPException(status_code=401, detail="账号不存在或已被禁用，请重新登录")
    # 用户端分享令牌只读：标记后 require_user 会拒绝管理类接口
    request.state.widget_scope = payload.get("scope") == "widget"
    return user


def require_user(request: Request, user: User | None = Depends(optional_user)) -> User:
    if not user:
        raise HTTPException(status_code=401, detail="请先登录（登录状态已失效）")
    if getattr(request.state, "widget_scope", False):
        raise HTTPException(status_code=403, detail="该链接仅用于用户端问答，不能访问管理后台")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


# ---------------- 用户序列化 ----------------
def user_to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "phone": user.phone,
        "nickname": user.nickname or "",
        "role": user.role,
        "disabled": bool(user.disabled),
        "login_count": user.login_count or 0,
        "last_login_at": user.last_login_at.isoformat(sep=" ", timespec="seconds")
        if user.last_login_at else None,
        "created_at": user.created_at.isoformat(sep=" ", timespec="seconds")
        if user.created_at else None,
    }
