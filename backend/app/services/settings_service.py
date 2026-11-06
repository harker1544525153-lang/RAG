# -*- coding: utf-8 -*-
"""
运行时设置服务：把「设置」页可修改的参数按账号持久化到 user_settings 表，
并合并 .env 默认值后作为生效配置输出。

优先级：user_settings（当前登录账号保存） > backend/.env（默认回退）
读取方式：服务内部统一用 get_runtime()，它通过 contextvar 识别当前请求账号，
避免每次问答都查库；设置页需要回显密钥当前值（保存过则显示已保存值，
否则显示 .env 默认值，都没有则为空），因此 describe() 会回传明文。
"""
import contextvars
import logging

from app.config import settings
from app import crud

logger = logging.getLogger(__name__)

# 当前请求所属账号：由 main.py 的 HTTP 中间件在进入路由前设置
_current_user: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "gala_current_user", default=None
)

# 账号 -> 生效配置（进程内缓存，保存设置后立即刷新该账号）
_runtimes: dict[str | None, dict] = {}


def set_current_user(user_id: str | None):
    return _current_user.set(user_id)


def reset_current_user(token) -> None:
    _current_user.reset(token)


def current_user_id() -> str | None:
    return _current_user.get()


def _llm_env_key() -> str:
    return settings.DASHCOPE_API_KEY or settings.DEEPSEEK_API_KEY


def _llm_env_base() -> str:
    # 未配置任何 Key 时按模型名推断默认接口地址，避免「qwen-plus + DeepSeek 地址」错配
    if settings.DASHCOPE_API_KEY or settings.LLM_MODEL.lower().startswith(("qwen", "qwq")):
        return settings.DASHCOPE_BASE_URL
    return settings.DEEPSEEK_BASE_URL


# 可编辑项定义：type / 取值范围 / 分组 / 展示元信息
SPECS: dict[str, dict] = {
    # ---------------- 大模型（OpenAI 兼容协议，可切换任意厂商） -------------
    "llm_api_key": {
        "type": str, "secret": True, "group": "llm", "label": "API Key",
        "hint": "默认显示当前生效的 Key（管理端保存值优先，其次 .env）；清空后保存即删除该 Key，调用会因缺少 Key 而失败",
    },
    "llm_base_url": {
        "type": str, "group": "llm", "label": "接口地址（Base URL）",
        "hint": "需为 OpenAI 兼容地址，如百炼 compatible-mode/v1、DeepSeek https://api.deepseek.com",
    },
    "llm_model": {
        "type": str, "group": "llm", "label": "模型名称",
        "hint": "如 qwen-plus、qwen-max、deepseek-chat、gpt-4o-mini",
    },
    "llm_temperature": {
        "type": float, "min": 0.0, "max": 2.0, "step": 0.1,
        "group": "llm", "label": "温度（随机性）", "hint": "0 更严谨，1 更发散；问答建议 0.1 ~ 0.5",
    },

    # ---------------- 向量化 -------------
    "embedding_provider": {
        "type": str, "options": ["local", "openai", "hash"], "group": "embedding",
        "label": "向量化方式", "hint": "openai = 云端 Embedding API；local = 本地 BGE；hash 仅联调用",
    },
    "embedding_base_url": {
        "type": str, "group": "embedding", "label": "向量接口地址",
        "hint": "OpenAI 兼容 Embedding 地址，如 https://api.siliconflow.cn/v1",
    },
    "embedding_api_model": {
        "type": str, "group": "embedding", "label": "向量模型名",
        "hint": "如 text-embedding-v4、qwen3.7-text-embedding-flash、BAAI/bge-m3",
    },
    "embedding_api_key": {
        "type": str, "secret": True, "group": "embedding", "label": "向量 API Key",
        "hint": "默认显示当前生效的 Key；清空后保存即删除；未配置时自动复用大模型 API Key（同为 OpenAI 兼容协议）",
    },
    "embedding_local_model": {
        "type": str, "group": "embedding", "label": "本地模型名",
        "hint": "仅在向量化方式为 local 时生效，如 BAAI/bge-large-zh-v1.5",
    },
    "embedding_batch_size": {
        "type": int, "min": 1, "max": 20, "group": "embedding",
        "label": "批量大小", "unit": "条", "hint": "单次请求的文本条数，百炼等接口上限 20",
    },

    # ---------------- 联网搜索（Tavily） -------------
    "tavily_api_key": {
        "type": str, "secret": True, "group": "search", "label": "Tavily API Key",
        "hint": "默认显示当前生效的 Key；清空后保存即删除，联网搜索会被跳过",
    },
    "tavily_base_url": {
        "type": str, "group": "search", "label": "搜索接口地址",
        "hint": "默认 https://api.tavily.com",
    },
    "web_search_enabled": {
        "type": bool, "group": "search", "label": "启用联网搜索",
        "hint": "开启后提问会调用 Tavily 搜索网络并并入回答上下文",
    },
    "web_search_mode": {
        "type": str, "options": ["always", "fallback"], "group": "search",
        "label": "搜索时机", "hint": "always 每次联网；fallback 仅知识库未命中时联网",
    },
    "web_search_max_results": {
        "type": int, "min": 1, "max": 10, "group": "search",
        "label": "返回网页条数", "unit": "条",
    },

    # ---------------- 检索与分块 -------------
    "chunk_size": {
        "type": int, "min": 100, "max": 2000, "group": "retrieval",
        "label": "分块大小", "unit": "字符", "hint": "仅影响之后新上传/重建的文档，500 ~ 1000 较通用",
    },
    "chunk_overlap": {
        "type": int, "min": 0, "max": 1000, "group": "retrieval",
        "label": "分块重叠", "unit": "字符", "hint": "需小于分块大小，建议为分块大小的 20%",
    },
    "top_k": {
        "type": int, "min": 1, "max": 20, "group": "retrieval",
        "label": "召回条数", "unit": "条", "hint": "每次问答送入大模型的切片数量",
    },
    "score_threshold": {
        "type": float, "min": 0.0, "max": 1.0, "step": 0.05, "group": "retrieval",
        "label": "相似度阈值", "hint": "越高越严格；回答太慢/召回过少时可上调到 0.45 左右",
    },
    "hybrid_search_enabled": {
        "type": bool, "group": "retrieval", "label": "启用混合检索",
        "hint": "向量语义召回叠加 BM25 关键词召回，按 RRF 融合；编号、型号、专有名词等精确查询召回更准",
    },
    # ---------------- 数据分级与权重 -------------
    "tier_core_weight": {
        "type": float, "min": 0.5, "max": 2.0, "step": 0.05, "group": "retrieval",
        "label": "核心权威权重", "hint": "核心制度/官方手册检索排序加成，建议 1.1 ~ 1.3",
    },
    "tier_standard_weight": {
        "type": float, "min": 0.5, "max": 2.0, "step": 0.05, "group": "retrieval",
        "label": "普通参考权重", "hint": "普通业务文档的基础权重，通常保持 1.0",
    },
    "tier_archived_weight": {
        "type": float, "min": 0.3, "max": 1.5, "step": 0.05, "group": "retrieval",
        "label": "过期归档权重", "hint": "归档文档降权系数，建议 0.6 ~ 0.8，避免压制新资料",
    },
    "recency_half_life_days": {
        "type": int, "min": 30, "max": 3650, "group": "retrieval",
        "label": "时间权重半衰期", "unit": "天",
        "hint": "文档越新权重越高；每过 N 天权重减半，建议 180 ~ 540",
    },
    "recency_floor": {
        "type": float, "min": 0.5, "max": 1.0, "step": 0.05, "group": "retrieval",
        "label": "时间权重下限", "hint": "旧文档最低保留权重，防止长期有效资料被完全压制",
    },
    "filter_expired": {
        "type": bool, "group": "retrieval", "label": "过滤过期文档",
        "hint": "开启后超过有效期的非核心文档不参与检索；关闭则仅降权",
    },

    # ---------------- 信息分类（品牌标识，按账号独立保存） -------------
    "brand_admin_title": {
        "type": str, "group": "brand", "label": "后台管理名称",
        "hint": "管理端侧边栏显示的产品名称，留空则用内置默认值",
        "allow_empty": True,
    },
    "brand_admin_subtitle": {
        "type": str, "group": "brand", "label": "后台管理副标题",
        "hint": "显示在后台名称下方的一句话说明",
        "allow_empty": True,
    },
    "brand_admin_logo": {
        "type": str, "group": "brand", "label": "后台管理 Logo",
        "hint": "上传 PNG / JPG / SVG 图片，留空则使用内置图标",
        "allow_empty": True,
    },
    "brand_user_title": {
        "type": str, "group": "brand", "label": "用户端名称",
        "hint": "用户端页面顶部显示的产品名称",
        "allow_empty": True,
    },
    "brand_user_subtitle": {
        "type": str, "group": "brand", "label": "用户端副标题",
        "hint": "用户端名称下方的一句话说明",
        "allow_empty": True,
    },
    "brand_user_logo": {
        "type": str, "group": "brand", "label": "用户端 Logo",
        "hint": "上传 PNG / JPG / SVG 图片，留空则使用内置图标",
        "allow_empty": True,
    },
}

# 会改变向量空间、需要重建索引的配置项
EMBEDDING_INDEX_KEYS = ("embedding_provider", "embedding_base_url", "embedding_api_model", "embedding_local_model")
# 会改变切片结果、需要重新分块并重建向量的配置项
CHUNK_INDEX_KEYS = ("chunk_size", "chunk_overlap")
REBUILD_KEYS = EMBEDDING_INDEX_KEYS + CHUNK_INDEX_KEYS


def _defaults() -> dict:
    """backend/.env 提供的默认值。"""
    return {
        "llm_api_key": _llm_env_key(),
        "llm_base_url": _llm_env_base(),
        "llm_model": settings.LLM_MODEL,
        "llm_temperature": settings.LLM_TEMPERATURE,

        "embedding_provider": settings.EMBEDDING_PROVIDER,
        "embedding_base_url": settings.EMBEDDING_BASE_URL,
        "embedding_api_model": settings.EMBEDDING_API_MODEL,
        "embedding_api_key": settings.EMBEDDING_API_KEY,
        "embedding_local_model": settings.EMBEDDING_MODEL,
        "embedding_batch_size": settings.EMBEDDING_BATCH_SIZE,

        "tavily_api_key": settings.TAVILY_API_KEY,
        "tavily_base_url": settings.TAVILY_BASE_URL,
        "web_search_max_results": settings.TAVILY_MAX_RESULTS,
        "web_search_enabled": settings.WEB_SEARCH_ENABLED,
        "web_search_mode": settings.WEB_SEARCH_MODE,

        "chunk_size": settings.CHUNK_SIZE,
        "chunk_overlap": settings.CHUNK_OVERLAP,
        "top_k": settings.TOP_K,
        "score_threshold": settings.SCORE_THRESHOLD,
        "hybrid_search_enabled": settings.HYBRID_SEARCH_ENABLED,

        # 数据分级与时间权重（默认值即可用，可在设置页调整）
        "tier_core_weight": 1.15,
        "tier_standard_weight": 1.0,
        "tier_archived_weight": 0.7,
        "recency_half_life_days": 365,
        "recency_floor": 0.75,
        "filter_expired": True,

        # 信息分类（品牌标识）：空值表示使用前端内置默认展示
        "brand_admin_title": "GALA 管理后台",
        "brand_admin_subtitle": "企业级 RAG 智能文档问答系统",
        "brand_admin_logo": "",
        "brand_user_title": "GALA 智能问答助手",
        "brand_user_subtitle": "基于企业知识库的智能问答",
        "brand_user_logo": "",
    }


def _coerce(key: str, raw):
    spec = SPECS[key]
    kind = spec["type"]
    if kind is bool:
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on", "开启")
    if kind is int:
        value = int(raw)
        if "min" in spec and value < spec["min"]:
            raise ValueError(f"{spec['label']} 不能小于 {spec['min']}")
        if "max" in spec and value > spec["max"]:
            raise ValueError(f"{spec['label']} 不能大于 {spec['max']}")
        return value
    if kind is float:
        value = float(raw)
        if "min" in spec and value < spec["min"]:
            raise ValueError(f"{spec['label']} 不能小于 {spec['min']}")
        if "max" in spec and value > spec["max"]:
            raise ValueError(f"{spec['label']} 不能大于 {spec['max']}")
        return value
    value = str(raw).strip()
    if spec.get("options") and value not in spec["options"]:
        raise ValueError(f"{spec['label']} 只能是 {' / '.join(spec['options'])}")
    if not value and not spec.get("secret") and not spec.get("allow_empty"):
        raise ValueError(f"{spec['label']} 不能为空")
    return value


def effective(db, user_id: str | None = None) -> dict:
    """生效配置：账号设置 + .env 默认（含明文密钥，仅供服务内部使用）。"""
    stored = crud.get_all_settings(db, user_id)
    result = _defaults()
    for key in SPECS:
        if key in stored:
            try:
                result[key] = _coerce(key, stored[key])
            except (ValueError, TypeError):
                logger.warning("忽略非法配置 %s=%r", key, stored[key])
    if result["web_search_enabled"] and not (result.get("tavily_api_key") or "").strip():
        result["web_search_enabled"] = False
    return result


def get_runtime(user_id: str | None = None) -> dict:
    """当前账号的生效配置；未指定时按 contextvar 取当前请求账号。

    具体账号缓存缺失（如刚注册尚未 refresh）时返回默认值，绝不回退管理员配置，
    避免新账号复用 admin 的 API Key / 检索参数。
    （user_id=None 表示未登录的站点公共访问，才沿用管理员配置。）
    """
    uid = current_user_id() if user_id is None else user_id
    if uid in _runtimes:
        return _runtimes[uid]
    if uid is not None:
        return _defaults()
    return _runtimes.get(None) or _defaults()


def refresh(db, user_id: str | None = None) -> dict:
    """刷新指定账号的运行时缓存（user_id=None 表示站点默认/旧全局配置）。"""
    uid = current_user_id() if user_id is None else user_id
    _runtimes[uid] = effective(db, uid)
    return _runtimes[uid]


def refresh_all(db) -> None:
    """启动时载入所有账号的设置，避免首个请求读到 .env 默认값。"""
    for uid in crud.list_user_ids(db):
        refresh(db, uid)
    # 站点默认（匿名访问、后台任务）沿用管理员配置
    admin_id = crud.get_default_admin_id(db)
    _runtimes[None] = _runtimes.get(admin_id) or effective(db, admin_id or None)


def _validate(cfg: dict) -> None:
    if cfg["chunk_overlap"] >= cfg["chunk_size"]:
        raise ValueError("分块重叠必须小于分块大小")
    # 没有 Tavily Key 时不允许启用联网搜索：自动降级为关闭（避免保存其他设置被阻塞）
    if cfg["web_search_enabled"] and not (cfg.get("tavily_api_key") or "").strip():
        cfg["web_search_enabled"] = False


def update(db, payload: dict, user_id: str | None = None) -> dict:
    """写入白名单内的配置（先校验后落库），返回 {config, rebuild_required}。"""
    uid = current_user_id() if user_id is None else user_id
    stored = crud.get_all_settings(db, uid)
    candidate = effective(db, uid)
    defaults = _defaults()
    saved: dict[str, str] = {}

    for key, raw in payload.items():
        spec = SPECS.get(key)
        if not spec:
            continue  # 未知字段直接忽略，兼容旧前端
        if raw is None:
            continue
        if raw == "":
            if not spec.get("secret") and not spec.get("allow_empty"):
                continue  # 非密钥：留空 = 不修改
            if not spec.get("allow_empty") and key not in stored and not defaults.get(key):
                continue  # 原本就没有 Key，无需写入
        value = _coerce(key, raw)
        candidate[key] = value
        # 布尔 false 也要落库，因此统一转字符串存储；空串表示已清除
        saved[key] = "true" if value is True else "false" if value is False else str(value)

    _validate(candidate)
    if not candidate["web_search_enabled"] and payload.get("web_search_enabled"):
        saved["web_search_enabled"] = "false"
    if saved:
        crud.set_settings(db, saved, uid)

    before = {key: _value_from(stored, key, defaults[key]) for key in REBUILD_KEYS}
    rebuild_required = any(str(candidate[key]) != str(before[key]) for key in REBUILD_KEYS)

    refresh(db, uid)
    logger.info("运行时配置已更新[%s]: %s", uid or "站点默认", ", ".join(sorted(saved)) or "无变更")
    return {"config": get_runtime(uid), "rebuild_required": rebuild_required}


def _value_from(stored: dict, key: str, default):
    if key in stored:
        try:
            return _coerce(key, stored[key])
        except (ValueError, TypeError):
            return default
    return default


def _kind_name(kind) -> str:
    if kind is int:
        return "int"
    if kind is float:
        return "float"
    if kind is bool:
        return "bool"
    return "str"


def _mask(secret: str) -> str:
    """密钥脱敏：只保留首尾各 4 位。"""
    if not secret:
        return ""
    if len(secret) <= 10:
        return "*" * len(secret)
    return f"{secret[:4]}****{secret[-4:]}"


def describe(db, user_id: str | None = None) -> dict:
    """设置页展示用：当前值 + 数据来源 + 只读系统状态。

    密钥字段回传当前生效值（管理端保存过的优先，否则取 .env，未配置为空串），
    便于设置页「默认读取已有 Key、可直接修改后保存」。
    """
    uid = current_user_id() if user_id is None else user_id
    stored = crud.get_all_settings(db, uid)
    cfg = effective(db, uid)
    defaults = _defaults()
    items = []
    for key, spec in SPECS.items():
        secret = bool(spec.get("secret"))
        value = cfg[key]
        item = {
            "key": key,
            "label": spec["label"],
            "group": spec["group"],
            "type": _kind_name(spec["type"]),
            "min": spec.get("min"),
            "max": spec.get("max"),
            "step": spec.get("step"),
            "unit": spec.get("unit") or "",
            "hint": spec.get("hint") or "",
            "options": spec.get("options"),
            "secret": secret,
            "source": "custom" if key in stored else "env",
        }
        # 密钥与非密钥都回传当前生效值；未配置时为空串
        item["value"] = (value or "") if secret else value
        item["default"] = defaults[key]
        if secret:
            item["masked"] = _mask(value)  # 供页面展示「当前已配置」状态
            item["configured"] = bool(value)
        items.append(item)

    provider = cfg["embedding_provider"]
    return {
        "editable": items,
        "system": {
            "database": "PostgreSQL" if settings.is_postgres else "SQLite",
            "database_url_masked": _mask_url(settings.DATABASE_URL),
            "vector_backend": settings.effective_vector_backend,
            "vector_collection": settings.VECTOR_COLLECTION,
            "llm_provider": settings.LLM_PROVIDER,
            "llm_model": cfg["llm_model"],
            "llm_base_url": cfg["llm_base_url"],
            "llm_api_key_configured": bool(cfg["llm_api_key"]),
            "embedding_provider": provider,
            "embedding_model": (
                cfg["embedding_api_model"] if provider == "openai" else cfg["embedding_local_model"]
            ),
            "embedding_api_key_configured": bool(
                cfg["embedding_api_key"] or (provider == "openai" and cfg["llm_api_key"])
            ),
            "embedding_dim": settings.EMBEDDING_DIM or "auto",
            "tavily_key_configured": bool(cfg["tavily_api_key"]),
            "seed_on_startup": settings.SEED_ON_STARTUP,
            "max_file_size_mb": settings.MAX_FILE_SIZE_MB,
            "allowed_extensions": sorted(settings.ALLOWED_EXTENSIONS),
            "data_dir": str(settings.DATA_DIR),
            "doc_dir": str(settings.DOC_DIR),
        },
    }


def _mask_url(url: str) -> str:
    """隐藏数据库连接串中的账号密码。"""
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        cred, tail = rest.rsplit("@", 1)
        if ":" in cred:
            user, _pwd = cred.split(":", 1)
            return f"{scheme}://{user}:***@{tail}"
        return f"{scheme}://***@{tail}"
    return url
