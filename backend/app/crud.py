# -*- coding: utf-8 -*-
"""数据访问层：文档 / 分类 / 会话 / 消息 / 运行时设置。"""
import json
import uuid
from datetime import datetime, date

from sqlalchemy import func, or_, select

from app.db import SessionLocal
from app.models import (
    AppSetting, Category, ChatMessage, ChatSession, Document, QaCategory, User, UserSetting,
)
from app.services import category_service


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def fmt_dt(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


# ---------------- 分类 ----------------

DEFAULT_CATEGORIES = [
    # 分类即分块策略：同类文档采用同一套解析/切分方式，避免「一刀切」导致截断与上下文断裂
    {"name": "通用文档", "description": "产品说明、常见资料（基础标准 512Token + 12% 重叠）",
     "color": "#165dff", "strategy": "standard", "chunk_size": 768, "chunk_overlap": 96},
    {"name": "Word 文档", "description": "长篇 Word 手册：章节摘要 + 细粒度子块（层级分块）",
     "color": "#00b42a", "strategy": "hierarchical", "chunk_size": 768, "chunk_overlap": 96},
    {"name": "制度文档", "description": "制度/合规条款：按标题、段落、条款边界语义切分",
     "color": "#ff7d00", "strategy": "semantic", "chunk_size": 768, "chunk_overlap": 120},
    {"name": "代码文档", "description": "代码/脚本：按函数、类、代码块结构切分，保持完整逻辑",
     "color": "#722ed1", "strategy": "code", "chunk_size": 1200, "chunk_overlap": 120},
    {"name": "表格文档", "description": "表格/清单：按行组切分并重复表头，保证列语义不丢失",
     "color": "#f53f3f", "strategy": "table", "chunk_size": 768, "chunk_overlap": 0},
    {"name": "流程文档", "description": "操作流程/审批流：按步骤边界切分，每块保留所属流程与上下文",
     "color": "#14c9c9", "strategy": "flow", "chunk_size": 768, "chunk_overlap": 96},
]

STRATEGY_LABELS = {
    "token": "按 Token 数",
    "standard": "基础标准",
    "semantic": "语义分块",
    "paragraph": "段落结构",
    "page": "按页切分",
    "hierarchical": "层级分块",
    "code": "代码结构",
    "table": "表格结构",
    "flow": "流程结构",
}

# 各策略实际使用的切分边界与说明：由后端统一下发，前端直接展示，避免文案与实现不一致
STRATEGY_META = {
    "token": {
        "boundary": "估算 Token 数（中文约 1.5 字 / Token）",
        "description": "以 Token 数为目标控制块体积，超长内容在同一元素内滑动切分。",
        "unit": "Token",
    },
    "standard": {
        "boundary": "字符长度（约 768 字符 ≈ 512 Token）+ 10%~15% 重叠",
        "description": "按长度递归切分并保留重叠，适配无明显结构特征的通用资料。",
        "unit": "字符",
    },
    "semantic": {
        "boundary": "标题 / 段落 / 条款边界（第 X 条、一、（一））",
        "description": "在标题与条款处断开，避免制度条文被截断。",
        "unit": "字符",
    },
    "paragraph": {
        "boundary": "标题切章节 + 空行分段落的段落边界",
        "description": "标题作为章节开头，段落为最小单位，短段落自动合并，超长段落再切。",
        "unit": "字符",
    },
    "page": {
        "boundary": "文件物理页码（同页不跨页）",
        "description": "按 PDF / PPT 页码分块，页内再按段落聚合，切片保留 page 定位。",
        "unit": "字符",
    },
    "hierarchical": {
        "boundary": "章节标题 + 子块长度",
        "description": "每个章节生成摘要父块，再细切子块，兼顾整体上下文与细节。",
        "unit": "字符",
    },
    "code": {
        "boundary": "函数 / 类 / 导入语句边界（保留缩进）",
        "description": "按代码结构切分，避免函数或类被截断。",
        "unit": "字符",
    },
    "table": {
        "boundary": "表头 + 行组边界（每块重复表头）",
        "description": "按行组切分并重复表头，保证每块列语义完整。",
        "unit": "字符",
    },
    "flow": {
        "boundary": "步骤标记（第 X 步 / 步骤 X / 1. / ①）",
        "description": "在步骤边界切分，每块保留所属流程标题与先后顺序。",
        "unit": "字符",
    },
}


def category_to_dict(c: Category, doc_count: int = 0) -> dict:
    strategy = getattr(c, "strategy", None) or "standard"
    meta = STRATEGY_META.get(strategy, {})
    return {
        "id": c.id,
        "name": c.name,
        "description": c.description or "",
        "color": c.color,
        "strategy": strategy,
        "strategy_label": STRATEGY_LABELS.get(strategy, "基础标准"),
        # 后端实际使用的切分边界依据与说明（上传弹窗、设置页直接展示）
        "strategy_boundary": meta.get("boundary", ""),
        "strategy_description": meta.get("description", ""),
        "size_unit": meta.get("unit", "字符"),
        "chunk_size": getattr(c, "chunk_size", None) or 768,
        "chunk_overlap": getattr(c, "chunk_overlap", None) or 0,
        "doc_count": doc_count,
        "created_at": fmt_dt(c.created_at),
    }


def list_categories(db, user_id: str = "") -> list[dict]:
    rows = db.execute(
        select(Category).where(Category.user_id == user_id).order_by(Category.created_at)
    ).scalars().all()
    counts = dict(
        db.execute(
            select(Document.category_id, func.count(Document.id))
            .where(Document.user_id == user_id)
            .group_by(Document.category_id)
        ).all()
    )
    return [category_to_dict(c, counts.get(c.id, 0)) for c in rows]


def get_category(db, category_id: str) -> Category | None:
    return db.get(Category, category_id)


def get_user_category(db, category_id: str, user_id: str) -> Category | None:
    """只返回属于该账号的分类，防止跨账号读写他人分类。"""
    cat = db.get(Category, category_id)
    if not cat or (cat.user_id or "") != (user_id or ""):
        return None
    return cat


def get_category_by_name(db, name: str, user_id: str = "") -> Category | None:
    return db.execute(
        select(Category).where(Category.name == name, Category.user_id == user_id)
    ).scalar_one_or_none()


def create_category(db, name: str, description: str = "", color: str = "#165dff",
                    strategy: str = "standard", chunk_size: int = 768,
                    chunk_overlap: int = 96, user_id: str = "") -> Category:
    cat = Category(
        id=new_id(), user_id=user_id, name=name.strip(), description=description, color=color,
        strategy=strategy, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def get_or_create_category(db, name: str, description: str = "", color: str = "#165dff",
                           user_id: str = "") -> Category:
    cat = get_category_by_name(db, name, user_id)
    if cat:
        return cat
    preset = next((c for c in DEFAULT_CATEGORIES if c["name"] == name), {})
    return create_category(
        db, name,
        description or preset.get("description", ""),
        preset.get("color", color),
        preset.get("strategy", "standard"),
        preset.get("chunk_size", 768),
        preset.get("chunk_overlap", 96),
        user_id=user_id,
    )


def update_category(db, category_id: str, **fields) -> Category | None:
    cat = db.get(Category, category_id)
    if not cat:
        return None
    for key in ("name", "description", "color", "strategy", "chunk_size", "chunk_overlap"):
        if fields.get(key) is not None:
            value = fields[key].strip() if isinstance(fields[key], str) else fields[key]
            setattr(cat, key, value)
    db.commit()
    db.refresh(cat)
    return cat


def delete_category(db, category_id: str, user_id: str = "") -> bool:
    cat = db.get(Category, category_id)
    if not cat or (cat.user_id or "") != (user_id or ""):
        return False
    db.execute(
        Document.__table__.update()
        .where(Document.category_id == category_id)
        .values(category_id=None)
    )
    db.delete(cat)
    db.commit()
    return True


def ensure_default_categories(db, user_id: str = "") -> None:
    """补齐推荐分类体系（该账号已有分类保留，不会删除用户数据）。"""
    existed = {
        c.name for c in db.execute(
            select(Category).where(Category.user_id == user_id)
        ).scalars().all()
    }
    for item in DEFAULT_CATEGORIES:
        if item["name"] not in existed:
            db.add(Category(id=new_id(), user_id=user_id, **item))
    # 旧版分类没有策略字段：回填默认策略，避免读取时为 None
    rows = db.execute(select(Category).where(Category.user_id == user_id)).scalars().all()
    for c in rows:
        if not getattr(c, "strategy", None):
            c.strategy = "standard"
        if not getattr(c, "chunk_size", None):
            c.chunk_size = 768
        if getattr(c, "chunk_overlap", None) is None:
            c.chunk_overlap = 96
    db.commit()


# ---------------- 文档 ----------------

def document_to_dict(d: Document, runtime: dict | None = None) -> dict:
    cat = d.category
    strategy = (getattr(cat, "strategy", None) or "standard") if cat else "standard"
    today = date.today()

    # 过期状态：expire_date < 今天视为已过期（expire_date 为空 = 长期有效）
    expire_date = getattr(d, "expire_date", None)
    effective_date = getattr(d, "effective_date", None)
    expired = False
    if expire_date:
        try:
            exp = datetime.strptime(str(expire_date)[:10], "%Y-%m-%d").date()
            expired = exp < today
        except ValueError:
            pass

    # 综合得分预览：分级权重 × 时间衰减 × 重要度系数（不含动态相似度）
    rt = runtime or {}
    tier_weights = {
        "core": float(rt.get("tier_core_weight", 1.15)),
        "standard": float(rt.get("tier_standard_weight", 1.0)),
        "archived": float(rt.get("tier_archived_weight", 0.7)),
    }
    half_life = max(30.0, float(rt.get("recency_half_life_days", 365) or 365))
    floor = max(0.5, float(rt.get("recency_floor", 0.75) or 0.75))
    tier_w = tier_weights.get(getattr(d, "tier", None) or "standard", 1.0)
    if expired:
        tier_w *= 0.6
    if effective_date:
        try:
            eff = datetime.strptime(str(effective_date)[:10], "%Y-%m-%d").date()
            age_days = max(0, (today - eff).days)
            recency_w = max(floor, 0.5 ** (age_days / half_life))
        except ValueError:
            recency_w = 1.0
    else:
        recency_w = 1.0
    importance = int(getattr(d, "importance", None) or 5)
    imp_w = 0.5 + max(1, min(10, importance)) * 0.1
    base_score = round(tier_w * recency_w * imp_w, 3)

    return {
        "id": d.id,
        "filename": d.filename,
        "file_type": d.file_type,
        "file_size": d.file_size,
        "chunk_count": d.chunk_count,
        "status": d.status,
        "error": d.error,
        "category_id": d.category_id,
        "category_name": cat.name if cat else None,
        "category_color": cat.color if cat else None,
        "strategy": strategy,
        "strategy_label": STRATEGY_LABELS.get(strategy, "基础标准"),
        "chunk_size": getattr(cat, "chunk_size", None) if cat else None,
        "chunk_overlap": getattr(cat, "chunk_overlap", None) if cat else None,
        "source": d.source,
        "tier": getattr(d, "tier", None) or "standard",
        "importance": importance,
        "auto_category": int(getattr(d, "auto_category", 0) or 0),
        "doc_type": getattr(d, "doc_type", "") or (cat.name if cat else ""),
        "module": getattr(d, "module", "") or "",
        "scenario": getattr(d, "scenario", "") or "",
        "visibility": getattr(d, "visibility", None) or "public",
        "effective_date": str(effective_date)[:10] if effective_date else None,
        "expire_date": str(expire_date)[:10] if expire_date else None,
        "effective_status": "expired" if expired else "active",
        "base_score": base_score,
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
    tier: str = "standard",
    importance: int = 5,
    doc_type: str = "",
    module: str = "",
    scenario: str = "",
    visibility: str = "public",
    effective_date: str | None = None,
    expire_date: str | None = None,
    auto_category: int = 0,
    user_id: str = "",
) -> Document:
    doc = Document(
        id=new_id(),
        user_id=user_id,
        filename=filename,
        file_type=file_type,
        file_size=file_size,
        status="processing",
        category_id=category_id,
        source=source,
        file_hash=file_hash,
        tier=tier,
        importance=importance,
        doc_type=doc_type,
        module=module,
        scenario=scenario,
        visibility=visibility,
        effective_date=effective_date,
        expire_date=expire_date,
        auto_category=auto_category,
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
        # 允许显式置空（如切回「默认配置」时清空 category_id）
        if hasattr(doc, key):
            setattr(doc, key, value)
    db.commit()
    db.refresh(doc)
    return doc


def list_documents(
    db,
    *,
    user_id: str = "",
    category_id: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    tier: str | None = None,
    module: str | None = None,
    scenario: str | None = None,
    visibility: str | None = None,
) -> list[dict]:
    stmt = select(Document).where(Document.user_id == user_id)
    if category_id:
        stmt = stmt.where(Document.category_id == category_id)
    if status:
        stmt = stmt.where(Document.status == status)
    if keyword:
        stmt = stmt.where(Document.filename.ilike(f"%{keyword}%"))
    if tier:
        stmt = stmt.where(Document.tier == tier)
    if module:
        stmt = stmt.where(Document.module == module)
    if scenario:
        stmt = stmt.where(Document.scenario == scenario)
    if visibility:
        stmt = stmt.where(Document.visibility == visibility)
    stmt = stmt.order_by(Document.created_at.desc())
    try:
        from app.services import settings_service
        runtime = settings_service.get_runtime()
    except Exception:
        runtime = {}
    return [document_to_dict(d, runtime=runtime) for d in db.execute(stmt).scalars().all()]


def get_document(db, doc_id: str) -> Document | None:
    return db.get(Document, doc_id)


def find_document_by_hash(db, file_hash: str, user_id: str = "") -> Document | None:
    return db.execute(
        select(Document).where(Document.file_hash == file_hash, Document.user_id == user_id)
    ).scalar_one_or_none()


def list_document_ids(db, user_id: str = "") -> list[str]:
    """该账号的文档 id 集合：向量检索按此过滤，避免召回其他账号的资料。"""
    return [
        r[0] for r in db.execute(
            select(Document.id).where(Document.user_id == user_id)
        ).all()
    ]


def delete_document(db, doc_id: str) -> None:
    doc = db.get(Document, doc_id)
    if doc:
        db.delete(doc)
        db.commit()


def document_stats(db, user_id: str = "") -> dict:
    scope = Document.user_id == user_id
    total = db.execute(select(func.count(Document.id)).where(scope)).scalar_one()
    total_chunks = db.execute(
        select(func.coalesce(func.sum(Document.chunk_count), 0)).where(scope)
    ).scalar_one()
    total_size = db.execute(
        select(func.coalesce(func.sum(Document.file_size), 0)).where(scope)
    ).scalar_one()
    ready = db.execute(
        select(func.count(Document.id)).where(scope, Document.status == "ready")
    ).scalar_one()
    processing = db.execute(
        select(func.count(Document.id)).where(scope, Document.status == "processing")
    ).scalar_one()
    failed = db.execute(
        select(func.count(Document.id)).where(scope, Document.status == "failed")
    ).scalar_one()

    by_type = [
        {"type": t, "count": c}
        for t, c in db.execute(
            select(Document.file_type, func.count(Document.id))
            .where(scope)
            .order_by(func.count(Document.id).desc())
        ).all()
    ]
    by_category = [
        {"category_id": cid, "name": name or "未分类", "color": color or "#86909c", "count": c}
        for cid, name, color, c in db.execute(
            select(Category.id, Category.name, Category.color, func.count(Document.id))
            .select_from(Document)
            .outerjoin(Category, Document.category_id == Category.id)
            .where(scope)
            .group_by(Category.id, Category.name, Category.color)
        ).all()
    ]
    by_tier = [
        {"tier": t or "standard", "count": c}
        for t, c in db.execute(
            select(Document.tier, func.count(Document.id))
            .where(scope)
            .group_by(Document.tier)
        ).all()
    ]
    return {
        "total_documents": total,
        "total_chunks": int(total_chunks or 0),
        "total_size": int(total_size or 0),
        "status": {"ready": ready, "processing": processing, "failed": failed},
        "by_type": by_type,
        "by_category": by_category,
        "by_tier": by_tier,
    }


# ---------------- 会话与消息 ----------------

def create_session(db, title: str = "新会话", source: str = "admin", visitor_name: str = "",
                   category: str | None = None, commit: bool = True,
                   user_id: str = "") -> str:
    sid = new_id()
    # 一个用户问题自动归入一个分类（分类与关键词在管理端「设置」维护，按账号独立）
    cate = category if category is not None else category_service.classify(db, title, user_id)
    db.add(
        ChatSession(
            id=sid,
            user_id=user_id,
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
                  limit: int = 200, user_id: str = "") -> list[dict]:
    stmt = (
        select(ChatSession)
        .where(ChatSession.user_id == user_id)
        .order_by(ChatSession.created_at.desc())
        .limit(limit)
    )
    if source:
        stmt = stmt.where(ChatSession.source == source)
    if category:
        stmt = stmt.where(ChatSession.category == category)
    rows = db.execute(stmt).scalars().all()
    meta = category_service.meta(db, user_id)
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
        # 用会话归属账号自己的问答分类规则归档
        s.category = category_service.classify(db, s.title, s.user_id or "")
    if rows:
        db.commit()
    return len(rows)


def get_session(db, session_id: str, user_id: str = "") -> ChatSession | None:
    """会话归属校验：只能访问当前账号自己的会话。"""
    s = db.get(ChatSession, session_id)
    if not s or (s.user_id or "") != (user_id or ""):
        return None
    return s


def delete_session(db, session_id: str, user_id: str = "") -> None:
    session = get_session(db, session_id, user_id)
    if session:
        db.delete(session)  # relationship cascade 自动删除消息
        db.commit()


def rename_session(db, session_id: str, title: str, user_id: str = "") -> None:
    session = get_session(db, session_id, user_id)
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


def list_messages(db, session_id: str, user_id: str | None = None) -> list[dict]:
    if user_id is not None and not get_session(db, session_id, user_id):
        return []
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

def get_all_settings(db, user_id: str | None = None) -> dict[str, str]:
    """读取账号设置；user_id=None 表示旧版全局配置（仅供历史兜底）。"""
    if user_id is None:
        return {s.key: s.value for s in db.execute(select(AppSetting)).scalars().all()}
    return {
        s.key: s.value for s in db.execute(
            select(UserSetting).where(UserSetting.user_id == user_id)
        ).scalars().all()
    }


def set_settings(db, values: dict[str, str], user_id: str | None = None) -> None:
    """写入设置；空值表示「清除」，直接删除该行，避免数据库残留空密钥记录。"""
    for key, value in values.items():
        text = "" if value is None else str(value)
        empty = not text.strip()
        if user_id is None:
            row = db.get(AppSetting, key)
            if empty:
                if row:
                    db.delete(row)
                continue
            if row:
                row.value = text
            else:
                db.add(AppSetting(key=key, value=text))
            continue
        row = db.get(UserSetting, (user_id, key))
        if empty:
            if row:
                db.delete(row)
            continue
        if row:
            row.value = text
        else:
            db.add(UserSetting(user_id=user_id, key=key, value=text))
    db.commit()


def list_user_ids(db) -> list[str]:
    return [r[0] for r in db.execute(select(User.id).order_by(User.created_at)).all()]


def get_default_admin_id(db) -> str | None:
    """站点默认配置的归属账号：第一个未禁用的管理员。"""
    return db.execute(
        select(User.id).where(User.role == "admin", User.disabled == 0).limit(1)
    ).scalar_one_or_none()


def backfill_admin_data(db, admin_id: str) -> None:
    """把旧版本（无 user_id）的存量数据归属到管理员账号。幂等：只处理空值行。"""
    from app.models import QaCategory

    db.execute(
        Category.__table__.update()
        .where(or_(Category.user_id.is_(None), Category.user_id == ""))
        .values(user_id=admin_id)
    )
    db.execute(
        Document.__table__.update()
        .where(or_(Document.user_id.is_(None), Document.user_id == ""))
        .values(user_id=admin_id)
    )
    # 会话：旧版数据补列后是空串，只在首次迁移时整体归管理员；
    # 之后新建的未登录会话（空串）属于站点公共会话，不再被吞并。
    migrated = db.get(AppSetting, "__user_scope_migrated__")
    if not migrated:
        db.execute(
            ChatSession.__table__.update()
            .where(or_(ChatSession.user_id.is_(None), ChatSession.user_id == ""))
            .values(user_id=admin_id)
        )
        db.add(AppSetting(key="__user_scope_migrated__", value="1"))
    # 问答分类迁移遗留的空串记录归管理员（该表不存在匿名语义）
    admin_ids = {
        r[0] for r in db.execute(
            select(QaCategory.id).where(QaCategory.user_id == admin_id)
        ).all()
    }
    for row in db.execute(select(QaCategory).where(QaCategory.user_id == "")).scalars().all():
        if row.id in admin_ids:
            db.delete(row)
        else:
            row.user_id = admin_id

    # 旧版全局设置（app_settings）归属管理员，成为各账号初始化的模板
    for key, value in get_all_settings(db, None).items():
        if key.startswith("__"):
            continue
        if db.get(UserSetting, (admin_id, key)):
            continue
        db.add(UserSetting(user_id=admin_id, key=key, value=value))
    db.commit()


def seed_user(db, user_id: str) -> None:
    """新账号初始化：只生成结构性默认分类，不复用管理员的任何设置。

    文档、问答记录、模型服务 API Key 等业务数据完全从零开始，各自的设置独立保存。
    """
    ensure_default_categories(db, user_id)
    category_service.ensure_seed(db, user_id)


def delete_user_data(db, user_id: str) -> dict:
    """删除账号时清理其专属数据（文档文件、向量切片、分类、会话）。"""
    from app.services.vector import get_vector_store
    from app.config import settings as app_settings

    store = get_vector_store()
    doc_ids = list_document_ids(db, user_id)
    for doc_id in doc_ids:
        try:
            store.delete_by_doc(doc_id)
        except Exception:
            pass
        for p in app_settings.UPLOAD_DIR.glob(f"{doc_id}_*"):
            p.unlink(missing_ok=True)
    db.execute(Document.__table__.delete().where(Document.user_id == user_id))
    db.execute(Category.__table__.delete().where(Category.user_id == user_id))
    db.execute(ChatSession.__table__.delete().where(ChatSession.user_id == user_id))
    db.execute(UserSetting.__table__.delete().where(UserSetting.user_id == user_id))
    db.execute(QaCategory.__table__.delete().where(QaCategory.user_id == user_id))
    db.commit()
    return {"documents": len(doc_ids)}


def clear_stale_processing(db) -> None:
    """服务异常退出后，重启时把残留的 processing 文档标记为 failed。"""
    stale = db.execute(select(Document).where(Document.status == "processing")).scalars().all()
    for d in stale:
        d.status = "failed"
        d.error = "服务重启，处理中断"
    if stale:
        db.commit()


def session_count(db, user_id: str = "") -> int:
    return db.execute(
        select(func.count(ChatSession.id)).where(ChatSession.user_id == user_id)
    ).scalar_one()


def message_count(db, user_id: str = "") -> int:
    """该账号会话下的消息总数（消息随会话归属用户）。"""
    return db.execute(
        select(func.count(ChatMessage.id))
        .select_from(ChatMessage)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(ChatSession.user_id == user_id)
    ).scalar_one()


def ensure_default_admin(db) -> User | None:
    """首次启动且 users 表为空时，创建默认管理员（手机号/密码见 .env 可覆盖）。"""
    from app.config import settings
    from app.security import hash_password

    existing_id = db.execute(select(User.id).order_by(User.created_at).limit(1)).scalar_one_or_none()
    if existing_id:
        return db.get(User, existing_id)
    admin = User(
        id=new_id(),
        phone=settings.DEFAULT_ADMIN_PHONE,
        password_hash=hash_password(settings.DEFAULT_ADMIN_PASSWORD),
        nickname="系统管理员",
        role="admin",
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin
