# -*- coding: utf-8 -*-
"""管理后台账号：图形验证码、注册、登录、当前用户。"""
import logging
import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app import crud
from app.models import User, UserLoginLog
from app.services import settings_service
from app.security import (
    check_captcha, create_captcha, create_token, hash_password, require_user,
    user_to_dict, verify_password,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


class RegisterPayload(BaseModel):
    phone: str = ""
    password: str = ""
    nickname: str = ""
    captcha_id: str = ""
    captcha_code: str = ""


class LoginPayload(BaseModel):
    phone: str = ""
    password: str = ""
    captcha_id: str = ""
    captcha_code: str = ""


def _client_info(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")[:255]
    return ip, ua


def _log_login(db: Session, user: User | None, phone: str, success: bool,
               reason: str, request: Request) -> None:
    ip, ua = _client_info(request)
    db.add(UserLoginLog(
        id=uuid.uuid4().hex[:16],
        user_id=user.id if user else None,
        phone=phone,
        success=1 if success else 0,
        reason=reason,
        ip=ip,
        user_agent=ua,
    ))
    db.commit()


def _validate_phone(phone: str) -> str:
    phone = (phone or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="请输入手机号")
    if not _PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="请输入正确的 11 位手机号")
    return phone


def _validate_password(password: str, *, for_register: bool = False) -> str:
    password = password or ""
    if not password:
        raise HTTPException(status_code=400, detail="请输入密码")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密码长度不能少于 6 位")
    if len(password) > 32:
        raise HTTPException(status_code=400, detail="密码长度不能超过 32 位")
    if for_register and any(ch.isspace() for ch in password):
        raise HTTPException(status_code=400, detail="密码不能包含空格")
    return password


def _validate_nickname(nickname: str) -> str:
    nickname = (nickname or "").strip()
    if len(nickname) > 20:
        raise HTTPException(status_code=400, detail="昵称最多 20 个字符")
    return nickname


def _check_captcha_or_raise(captcha_id: str, code: str) -> None:
    if not (captcha_id or "").strip():
        raise HTTPException(status_code=400, detail="图形验证码已失效，请点击图片刷新")
    if not (code or "").strip():
        raise HTTPException(status_code=400, detail="请输入图形验证码")
    if not check_captcha(captcha_id, code):
        raise HTTPException(status_code=400, detail="验证码错误或已过期，请重新输入")


@router.get("/captcha")
def captcha():
    cid, svg = create_captcha()
    return {"captcha_id": cid, "svg": svg, "expires_in": 300}


@router.get("/check-phone")
def check_phone(phone: str = "", db: Session = Depends(get_db)):
    """注册前预检：手机号格式是否合法、是否已被注册（不占用验证码）。"""
    phone = (phone or "").strip()
    if not phone:
        return {"phone": phone, "valid": False, "exists": False,
                "message": "请输入手机号"}
    if not _PHONE_RE.match(phone):
        return {"phone": phone, "valid": False, "exists": False,
                "message": "请输入正确的 11 位手机号"}
    exists = db.execute(select(User.id).where(User.phone == phone)).scalar_one_or_none() is not None
    return {
        "phone": phone,
        "valid": True,
        "exists": exists,
        "message": "该手机号已注册，请直接登录" if exists else "该手机号可以注册",
    }


@router.post("/register")
def register(payload: RegisterPayload, request: Request, db: Session = Depends(get_db)):
    phone = _validate_phone(payload.phone)
    password = _validate_password(payload.password, for_register=True)
    nickname = _validate_nickname(payload.nickname)
    _check_captcha_or_raise(payload.captcha_id, payload.captcha_code)

    exists = db.execute(select(User).where(User.phone == phone)).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=400, detail="该手机号已注册，请直接登录")

    nickname = nickname or f"用户{phone[-4:]}"
    user = User(
        id=uuid.uuid4().hex[:16],
        phone=phone,
        password_hash=hash_password(password),
        nickname=nickname,
        role="user",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    # 新账号初始化：复制管理员当前设置 + 生成自己的默认分类（与 admin 一致的初始配置）
    crud.seed_user(db, user.id)
    settings_service.refresh(db, user.id)

    _log_login(db, user, phone, True, "注册并登录", request)
    return {"token": create_token(user.id), "user": user_to_dict(user)}


@router.post("/login")
def login(payload: LoginPayload, request: Request, db: Session = Depends(get_db)):
    phone = _validate_phone(payload.phone)
    password = _validate_password(payload.password)
    _check_captcha_or_raise(payload.captcha_id, payload.captcha_code)

    user = db.execute(select(User).where(User.phone == phone)).scalar_one_or_none()
    if not user:
        _log_login(db, None, phone, False, "账号不存在", request)
        raise HTTPException(status_code=400, detail="该手机号尚未注册，请先注册新账号")
    if user.disabled:
        _log_login(db, user, phone, False, "账号已被禁用", request)
        raise HTTPException(status_code=403, detail="该账号已被禁用，请联系管理员")
    if not verify_password(password, user.password_hash):
        _log_login(db, user, phone, False, "密码错误", request)
        raise HTTPException(status_code=400, detail="密码错误，请重新输入")

    user.login_count = (user.login_count or 0) + 1
    user.last_login_at = datetime.now()
    db.commit()
    db.refresh(user)
    _log_login(db, user, phone, True, "登录成功", request)
    return {"token": create_token(user.id), "user": user_to_dict(user)}


@router.get("/me")
def me(user: User = Depends(require_user)):
    return {"user": user_to_dict(user)}


@router.post("/logout")
def logout():
    # 令牌为无状态签名，服务端不维护会话；前端清除本地 token 即可
    return {"ok": True}
