# -*- coding: utf-8 -*-
"""问答自动归档分类服务。

分类与关键词存在 `qa_categories` 表，管理端「设置」模块可增删改；
表为空时在启动阶段写入默认 6 类。

归档规则：按 sort_order 升序逐类匹配关键词，命中即返回，
保证「一个用户问题只归入一个分类」；全部未命中归入 other。
"""
import time
import uuid

from sqlalchemy import select

from app.models import ChatSession, QaCategory

# 默认分类（仅在表为空时初始化；之后以数据库为准）
DEFAULT_CATEGORIES = [
    {
        "id": "issue",
        "label": "故障异常",
        "color": "#c9302c",
        "sort_order": 1,
        "keywords": "报错,错误,失败,无法,不能,打不开,卡住,卡顿,闪退,崩溃,异常,失效,没反应,不生效,白屏,黑屏,bug,加载不出,登录不了,上传不了",
    },
    {
        "id": "account",
        "label": "账号权限",
        "color": "#e86a17",
        "sort_order": 2,
        "keywords": "登录,注册,账号,帐号,密码,权限,角色,实名,认证,审核,绑定,注销,授权,成员,管理员",
    },
    {
        "id": "data",
        "label": "数据报表",
        "color": "#6c5ce7",
        "sort_order": 3,
        "keywords": "数据,统计,报表,收益,结算,流水,账单,礼物,提现,充值,订单,金额,分成,收入,观看时长",
    },
    {
        "id": "operation",
        "label": "操作指引",
        "color": "#0bae6e",
        "sort_order": 4,
        "keywords": "步骤,操作,设置,创建,上传,删除,修改,添加,配置,开启,关闭,流程,入口,在哪,开播",
    },
    {
        "id": "feature",
        "label": "功能咨询",
        "color": "#165dff",
        "sort_order": 5,
        "keywords": "功能,模块,支持,可以,有哪些,是什么,做什么,介绍,说明,作用,用途,区别",
    },
    {
        "id": "other",
        "label": "其他",
        "color": "#8a94a6",
        "sort_order": 99,
        "keywords": "",
    },
]

_CACHE_TTL = 15.0
_cache: dict = {"ts": 0.0, "rules": []}


def _invalidate() -> None:
    _cache["ts"] = 0.0


def ensure_seed(db) -> None:
    """表为空时写入默认分类（不覆盖用户已配置的内容）。"""
    if db.execute(select(QaCategory.id).limit(1)).first():
        return
    for item in DEFAULT_CATEGORIES:
        db.add(QaCategory(**item))
    db.commit()
    _invalidate()


def _rules(db) -> list[tuple[str, list[str]]]:
    now = time.time()
    if now - _cache["ts"] < _CACHE_TTL:
        return _cache["rules"]
    rows = (
        db.execute(
            select(QaCategory)
            .where(QaCategory.enabled == 1)
            .order_by(QaCategory.sort_order, QaCategory.id)
        )
        .scalars()
        .all()
    )
    rules = [
        (r.id, [k.strip() for k in (r.keywords or "").split(",") if k.strip()])
        for r in rows
    ]
    _cache.update(ts=now, rules=rules)
    return rules


def classify(db, question: str) -> str:
    """把一个问题归入唯一分类 id。"""
    text = (question or "").strip()
    if not text:
        return "other"
    for cid, keywords in _rules(db):
        if cid == "other" or not keywords:
            continue
        if any(k in text for k in keywords):
            return cid
    return "other"


def list_all(db) -> list[dict]:
    rows = (
        db.execute(select(QaCategory).order_by(QaCategory.sort_order, QaCategory.id))
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "label": r.label,
            "color": r.color,
            "keywords": r.keywords or "",
            "sort_order": r.sort_order,
            "enabled": bool(r.enabled),
        }
        for r in rows
    ]


def meta(db) -> dict[str, dict]:
    """id -> {label, color}，供会话列表映射显示。"""
    return {c["id"]: {"label": c["label"], "color": c["color"]} for c in list_all(db)}


def _normalize_id(raw: str) -> str:
    cid = (raw or "").strip()
    if not cid:
        cid = "c" + uuid.uuid4().hex[:8]
    if len(cid) > 16 or not all(ch.isalnum() or ch in "-_" for ch in cid):
        raise ValueError("分类标识只能包含字母、数字、- 和 _，且不超过 16 个字符")
    return cid


def create(db, data: dict) -> dict:
    cid = _normalize_id(data.get("id", ""))
    if db.get(QaCategory, cid):
        raise ValueError(f"分类标识 {cid} 已存在")
    row = QaCategory(
        id=cid,
        label=(data.get("label") or "").strip() or cid,
        color=data.get("color") or "#165dff",
        keywords=data.get("keywords") or "",
        sort_order=int(data.get("sort_order") or 0),
        enabled=1 if data.get("enabled", True) else 0,
    )
    db.add(row)
    db.commit()
    _invalidate()
    return {"id": cid}


def update(db, cid: str, data: dict) -> dict:
    row = db.get(QaCategory, cid)
    if not row:
        raise ValueError("分类不存在")
    if "label" in data and data["label"] is not None:
        row.label = str(data["label"]).strip() or row.label
    if "color" in data and data["color"]:
        row.color = data["color"]
    if "keywords" in data and data["keywords"] is not None:
        row.keywords = data["keywords"]
    if "sort_order" in data and data["sort_order"] is not None:
        row.sort_order = int(data["sort_order"])
    if "enabled" in data and data["enabled"] is not None:
        row.enabled = 1 if data["enabled"] else 0
    db.commit()
    _invalidate()
    return {"id": cid}


def delete(db, cid: str) -> None:
    if cid == "other":
        raise ValueError("「其他」为兜底分类，不能删除")
    row = db.get(QaCategory, cid)
    if not row:
        raise ValueError("分类不存在")
    # 历史会话回落到「其他」，避免出现无对应分类的孤儿标签
    db.query(ChatSession).filter(ChatSession.category == cid).update({"category": "other"})
    db.delete(row)
    db.commit()
    _invalidate()
