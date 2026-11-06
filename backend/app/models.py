# -*- coding: utf-8 -*-
"""
SQLAlchemy ORM 模型（SQLite / PostgreSQL 通用）。

表结构：
  categories      知识库分类
  documents       文档元信息（含分类、来源、哈希去重）
  chat_sessions   会话
  chat_messages   消息记录（sources 为 JSON 文本）
  app_settings    运行时配置覆盖（设置页可修改并持久化）
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now()


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # 归属用户：各账号的知识库分类互不可见（空串 = 站点默认/匿名）
    user_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str] = mapped_column(String(16), default="#165dff")
    # 分块策略：token 按 Token / standard 基础 / semantic 语义条款 / paragraph 段落标题 /
    # page 按页码 / hierarchical 层级摘要 / code 代码 / table 表格 / flow 流程
    strategy: Mapped[str] = mapped_column(String(16), default="standard")
    chunk_size: Mapped[int] = mapped_column(Integer, default=768)   # 约 512 Token（中文 1.5 字/Token）
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=96)  # 约 12.5% 重叠
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    documents: Mapped[list["Document"]] = relationship(back_populates="category")


class QaCategory(Base):
    """问答自动归档分类（管理端「设置」可增删改），按账号独立配置。"""

    __tablename__ = "qa_categories"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True, default="")
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    color: Mapped[str] = mapped_column(String(16), default="#165dff")
    keywords: Mapped[str] = mapped_column(Text, default="")  # 英文逗号分隔
    sort_order: Mapped[int] = mapped_column(Integer, default=0)  # 越小优先级越高
    enabled: Mapped[int] = mapped_column(Integer, default=1)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # 归属用户：文档管理、检索范围按账号隔离
    user_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="processing")  # processing/ready/failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("categories.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="upload")  # upload / seed
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 原文渲染后的 HTML（入库时生成）：云端原文件丢失时仍能按原格式预览并锚定引用片段
    preview_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 是否由「默认配置」按内容自动识别分类（1=自动识别，0=手动指定）
    auto_category: Mapped[int] = mapped_column(Integer, default=0)
    # ---------------- 结构化元数据（检索过滤与权重） ----------------
    # 数据分级：core 核心权威 / standard 普通参考 / archived 过期归档
    tier: Mapped[str] = mapped_column(String(16), default="standard")
    importance: Mapped[int] = mapped_column(Integer, default=5)  # 1~10，越大越优先
    doc_type: Mapped[str] = mapped_column(String(32), default="")  # 文档类型标签（默认取分类名）
    module: Mapped[str] = mapped_column(String(80), default="")    # 业务模块
    scenario: Mapped[str] = mapped_column(String(80), default="")  # 适用场景
    visibility: Mapped[str] = mapped_column(String(16), default="public")  # public/internal/admin
    effective_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    expire_date: Mapped[str | None] = mapped_column(String(10), nullable=True)     # 过期后默认降权/过滤
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    category: Mapped[Category | None] = relationship(back_populates="documents")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # 归属用户：问答记录按账号隔离（空串 = 未登录的站点公共会话）
    user_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    title: Mapped[str] = mapped_column(String(255), default="新会话")
    source: Mapped[str] = mapped_column(String(16), default="admin")  # admin / user
    visitor_name: Mapped[str] = mapped_column(String(80), default="")
    category: Mapped[str] = mapped_column(String(16), default="")  # 问答自动归档分类
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user / assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 数组
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class UserSetting(Base):
    """按账号保存的运行时设置（模型服务、检索参数、品牌信息等）。

    与 app_settings 的区别：app_settings 是旧版全局配置，仅作为历史兜底；
    新逻辑一律按 user_id 读写，保证各账号设置互不影响。
    """

    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class User(Base):
    """管理后台账号：手机号 + 密码登录，role=admin 额外拥有用户管理与对话统计。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)  # salt$pbkdf2
    nickname: Mapped[str] = mapped_column(String(60), default="")
    role: Mapped[str] = mapped_column(String(16), default="user")  # user / admin
    disabled: Mapped[int] = mapped_column(Integer, default=0)  # 1 = 禁止登录
    login_count: Mapped[int] = mapped_column(Integer, default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class UserLoginLog(Base):
    """登录记录：成功与失败都记录，供用户管理查看。"""

    __tablename__ = "user_login_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    phone: Mapped[str] = mapped_column(String(20), default="")
    success: Mapped[int] = mapped_column(Integer, default=1)
    reason: Mapped[str] = mapped_column(String(120), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
