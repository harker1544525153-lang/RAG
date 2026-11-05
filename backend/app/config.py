# -*- coding: utf-8 -*-
"""
全局配置：所有可调参数集中在这里，支持 .env 环境变量覆盖。

设计要点（面向云端迁移）：
- 业务数据库统一走 DATABASE_URL：
    本地默认  sqlite:///backend/data/rag.db
    云端      postgresql+psycopg://user:pass@host/db（Neon / Supabase 免费层）
- 向量库自动匹配：SQLite -> ChromaDB（本地落盘），Postgres -> pgvector（同一库）
- 向量化模型可切换：local（本地 BGE）/ openai（OpenAI 兼容 Embedding API）/ hash（离线测试）
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# backend/ 与项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR.parent
load_dotenv(BASE_DIR / ".env")


def _env_path(key: str, default: Path) -> Path:
    raw = os.getenv(key)
    return Path(raw).expanduser().resolve() if raw else default


# Serverless（Vercel）环境：仓库目录只读，数据目录只能写到 /tmp（实例重启即清空）
_IS_SERVERLESS = bool(os.getenv("VERCEL"))


def _normalize_db_url(url: str) -> str:
    """补全 Postgres driver：Neon/Supabase 控制台给的是 postgres:// 或 postgresql://，
    默认会找 psycopg2；本项目安装的是 psycopg3，不补全会让 create_engine 直接抛
    ImportError，导致 Vercel 全站 500。"""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Settings:
    # ---------- 基础路径 ----------
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8080"))

    # 数据目录（上传文件 / SQLite / 本地向量库统一放这里，便于整体备份）
    # Vercel 上默认 /tmp/rag-data；业务库请配 DATABASE_URL（Neon/Postgres）
    DATA_DIR: Path = _env_path("DATA_DIR", Path("/tmp/rag-data") if _IS_SERVERLESS else BASE_DIR / "data")
    UPLOAD_DIR: Path = DATA_DIR / "uploads"
    CHROMA_DIR: Path = DATA_DIR / "chroma"
    DB_PATH: Path = DATA_DIR / "rag.db"

    # 初始知识库文档目录（随仓库提交，启动时自动导入）
    DOC_DIR: Path = _env_path("DOC_DIR", PROJECT_DIR / "DOC")

    # ---------- 业务数据库 ----------
    # 本地：sqlite:///C:/xxx/backend/data/rag.db
    # 云端：postgresql+psycopg://USER:PASS@HOST/DB?sslmode=require
    # Neon/Supabase 原样复制过来的 postgres:// / postgresql:// 会被自动补成 +psycopg
    DATABASE_URL: str = _normalize_db_url(
        os.getenv("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'rag.db').as_posix()}")
    )

    # ---------- 向量库后端 ----------
    # auto：按 DATABASE_URL 自动选择（sqlite -> sqlite 内置向量表，postgres -> pgvector）
    # 可选值：auto / sqlite / chroma / pgvector
    VECTOR_BACKEND: str = os.getenv("VECTOR_BACKEND", "auto")
    # pgvector 集合名（同一数据库内多租户/多知识库时可分表名区分）
    VECTOR_COLLECTION: str = os.getenv("VECTOR_COLLECTION", "rag_docs")

    # ---------- 允许上传的文件类型 ----------
    ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".pptx"}
    MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "50"))

    # ---------- 分块策略（默认值，可在「设置」页运行时覆盖） ----------
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "600"))        # 500~1000 字符
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "120"))  # 约 20% 重叠，防跨段语义断裂

    # ---------- 向量化模型（默认值，可在「设置」页运行时覆盖） ----------
    # local  本地开源 BGE（sentence-transformers，首次自动下载）
    # openai OpenAI 兼容 Embedding API（SiliconFlow / 智谱 / OpenAI 等）
    # hash   确定性哈希向量，仅用于离线联调与 CI（无外部依赖）
    # Vercel 等 Serverless 环境装不下本地模型，默认走 OpenAI 兼容 Embedding API
    EMBEDDING_PROVIDER: str = os.getenv("EMBEDDING_PROVIDER", "openai" if _IS_SERVERLESS else "local")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5")
    EMBEDDING_DEVICE: str = os.getenv("EMBEDDING_DEVICE", "cpu")
    EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "0"))  # 0 = 按模型自动推断
    EMBEDDING_BATCH_SIZE: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))

    # openai 兼容模式（EMBEDDING_PROVIDER=openai 时生效）
    # EMBEDDING_API_KEY 留空则复用 DASHCOPE_API_KEY（同属 OpenAI 兼容协议，无需重复填 Key）
    EMBEDDING_API_KEY: str = os.getenv("EMBEDDING_API_KEY", "")
    EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1")
    # 留空时：复用百炼则默认 text-embedding-v4，否则必须在 .env 指定模型名
    EMBEDDING_API_MODEL: str = os.getenv("EMBEDDING_API_MODEL", "")

    # ---------- 检索参数（默认值，可在「设置」页运行时覆盖） ----------
    TOP_K: int = int(os.getenv("TOP_K", "5"))
    SCORE_THRESHOLD: float = float(os.getenv("SCORE_THRESHOLD", "0.35"))

    # ---------- LLM（OpenAI 兼容协议，支持多家厂商） ----------
    # 优先级：阿里云百炼 DASHCOPE_* > DeepSeek DEEPSEEK_*
    _use_dashscope = bool(os.getenv("DASHCOPE_API_KEY"))
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "dashscope" if _use_dashscope else "deepseek")
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DASHCOPE_API_KEY: str = os.getenv("DASHCOPE_API_KEY", "")
    DASHCOPE_BASE_URL: str = os.getenv(
        "DASHCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    LLM_MODEL: str = os.getenv("LLM_MODEL", "qwen-plus" if _use_dashscope else "deepseek-chat")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.3"))

    # ---------- 联网搜索（Tavily，管理端「设置」可开启） ----------
    # Key 只放 .env；管理端只控制开关、模式与条数（存 app_settings，即时生效）
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
    TAVILY_BASE_URL: str = os.getenv("TAVILY_BASE_URL", "https://api.tavily.com")
    TAVILY_MAX_RESULTS: int = int(os.getenv("TAVILY_MAX_RESULTS", "5"))
    WEB_SEARCH_ENABLED: bool = os.getenv("WEB_SEARCH_ENABLED", "false").lower() in (
        "1", "true", "yes", "on",
    )
    # always：每次提问都联网；fallback：仅知识库未命中时联网
    WEB_SEARCH_MODE: str = os.getenv("WEB_SEARCH_MODE", "fallback")

    # ---------- 启动行为 ----------
    # Serverless 冷启动时不做后台导入（耗时长且可能被冻结），默认关闭
    SEED_ON_STARTUP: bool = os.getenv(
        "SEED_ON_STARTUP", "false" if _IS_SERVERLESS else "true"
    ).lower() in ("1", "true", "yes", "on")

    # ---------- HuggingFace 网络与离线 ----------
    # 国内直连 huggingface.co 会超时重试数十秒，导致首次提问长时间无响应
    HF_ENDPOINT: str = os.getenv("HF_ENDPOINT", "https://hf-mirror.com")
    HF_HUB_OFFLINE: bool = os.getenv("HF_HUB_OFFLINE", "false").lower() in ("1", "true", "yes", "on")
    TRANSFORMERS_OFFLINE: bool = os.getenv("TRANSFORMERS_OFFLINE", "false").lower() in ("1", "true", "yes", "on")

    @property
    def is_postgres(self) -> bool:
        return self.DATABASE_URL.startswith("postgres")

    @property
    def effective_vector_backend(self) -> str:
        if self.VECTOR_BACKEND in ("sqlite", "chroma", "pgvector"):
            return self.VECTOR_BACKEND
        return "pgvector" if self.is_postgres else "sqlite"

    def __repr__(self) -> str:  # 方便启动时打印
        return (
            f"<Settings db={'postgres' if self.is_postgres else 'sqlite'} "
            f"vector={self.effective_vector_backend} embedding={self.EMBEDDING_PROVIDER} "
            f"llm={self.LLM_MODEL}>"
        )


settings = Settings()

# 把 HuggingFace 相关配置同步到进程环境变量：
# huggingface_hub 在导入时读取这些变量，必须在加载模型前设置好
if settings.HF_ENDPOINT:
    os.environ.setdefault("HF_ENDPOINT", settings.HF_ENDPOINT)
if settings.HF_HUB_OFFLINE:
    os.environ["HF_HUB_OFFLINE"] = "1"
if settings.TRANSFORMERS_OFFLINE:
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
# 即使在线模式也缩短元数据检查超时，避免国内网络重试数十秒
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "5")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")

# 确保本地数据目录存在（只读环境如 Vercel 忽略失败，上传文件改用 /tmp 或外部存储）
for _d in (settings.DATA_DIR, settings.UPLOAD_DIR, settings.CHROMA_DIR):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
