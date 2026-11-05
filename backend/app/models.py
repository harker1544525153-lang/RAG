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
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str] = mapped_column(String(16), default="#165dff")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    documents: Mapped[list["Document"]] = relationship(back_populates="category")


class QaCategory(Base):
    """问答自动归档分类（管理端「设置」可增删改）。"""

    __tablename__ = "qa_categories"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    color: Mapped[str] = mapped_column(String(16), default="#165dff")
    keywords: Mapped[str] = mapped_column(Text, default="")  # 英文逗号分隔
    sort_order: Mapped[int] = mapped_column(Integer, default=0)  # 越小优先级越高
    enabled: Mapped[int] = mapped_column(Integer, default=1)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    category: Mapped[Category | None] = relationship(back_populates="documents")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
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
