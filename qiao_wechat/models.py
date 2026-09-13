from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class AccountStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"


class AccountType(str, enum.Enum):
    subscription = "subscription"
    service = "service"
    unknown = "unknown"


class ArticleStatus(str, enum.Enum):
    created = "created"
    researched = "researched"
    written = "written"
    reviewed = "reviewed"
    rendered = "rendered"
    draft_created = "draft_created"
    preview_sent = "preview_sent"
    publish_submitted = "publish_submitted"
    published = "published"
    failed = "failed"


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"


class PublishChannel(str, enum.Enum):
    local_preview = "local_preview"
    wx_draft = "wx_draft"
    wx_preview = "wx_preview"
    wx_freepublish = "wx_freepublish"
    wx_mass_send = "wx_mass_send"
    xhs_export = "xhs_export"


class WeChatAccount(Base):
    __tablename__ = "wechat_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    appid: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    # In production prefer secret_env_name. raw_secret is allowed for local demos.
    raw_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_env_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    account_type: Mapped[AccountType] = mapped_column(Enum(AccountType), default=AccountType.unknown, nullable=False)
    is_certified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    author: Mapped[str | None] = mapped_column(String(64), nullable=True)
    default_cover_media_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[AccountStatus] = mapped_column(Enum(AccountStatus), default=AccountStatus.active, nullable=False)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    tokens: Mapped[list[AccessToken]] = relationship("AccessToken", back_populates="account", cascade="all,delete-orphan")
    articles: Mapped[list[Article]] = relationship("Article", back_populates="account")


class AccessToken(Base):
    __tablename__ = "access_tokens"
    __table_args__ = (UniqueConstraint("account_id", "token_type", name="uq_account_token_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("wechat_accounts.id"), nullable=False, index=True)
    token_type: Mapped[str] = mapped_column(String(32), default="stable", nullable=False)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(SQLiteJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    account: Mapped[WeChatAccount] = relationship("WeChatAccount", back_populates="tokens")


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (UniqueConstraint("account_id", "sha256", "purpose", name="uq_asset_account_sha_purpose"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("wechat_accounts.id"), nullable=True, index=True)
    purpose: Mapped[str] = mapped_column(String(32), default="inline", nullable=False)  # cover / inline / xhs
    source: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mime_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    media_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    wx_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(SQLiteJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("wechat_accounts.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    author: Mapped[str | None] = mapped_column(String(64), nullable=True)
    digest: Mapped[str | None] = mapped_column(String(120), nullable=True)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    html: Mapped[str | None] = mapped_column(Text, nullable=True)
    xhs_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_media_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    content_source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    theme: Mapped[str] = mapped_column(String(80), default="wechat_baseline", nullable=False)
    status: Mapped[ArticleStatus] = mapped_column(Enum(ArticleStatus), default=ArticleStatus.created, nullable=False)
    local_preview_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    wx_draft_media_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    wx_publish_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    wx_article_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    wx_article_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(SQLiteJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    account: Mapped[WeChatAccount | None] = relationship("WeChatAccount", back_populates="articles")
    jobs: Mapped[list[PublishJob]] = relationship("PublishJob", back_populates="article", cascade="all,delete-orphan")


class PublishJob(Base):
    __tablename__ = "publish_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), nullable=False, index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("wechat_accounts.id"), nullable=True, index=True)
    channel: Mapped[PublishChannel] = mapped_column(Enum(PublishChannel), nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.pending, nullable=False)
    request_json: Mapped[dict[str, Any] | None] = mapped_column(SQLiteJSON, nullable=True)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(SQLiteJSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Failure classification, e.g. remote_rejected / remote_result_unknown /
    # remote_unconfirmed / validation_failed / local_error. Kept as plain String
    # (no enum CHECK) so old SQLite databases only need an ADD COLUMN migration.
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Stable idempotency key for remote actions (sha256 over account + action +
    # content revision). Nullable: only channels with remote side effects set it.
    request_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    article: Mapped[Article] = relationship("Article", back_populates="jobs")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(SQLiteJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
