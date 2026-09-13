from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .models import AccountStatus, AccountType, ArticleStatus, JobStatus, PublishChannel


class AccountCreate(BaseModel):
    name: str
    appid: str
    raw_secret: str | None = None
    secret_env_name: str | None = None
    account_type: AccountType = AccountType.unknown
    is_certified: bool = False
    author: str | None = None
    default_cover_media_id: str | None = None
    remark: str | None = None


class AccountOut(BaseModel):
    id: int
    name: str
    appid: str
    account_type: AccountType
    is_certified: bool
    author: str | None
    default_cover_media_id: str | None
    status: AccountStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class ArticleCreate(BaseModel):
    account_id: int | None = None
    title: str = Field(max_length=32)
    markdown: str
    author: str | None = None
    digest: str | None = Field(default=None, max_length=128)
    cover_source: str | None = None
    content_source_url: str | None = None
    theme: str = "wechat_baseline"
    meta: dict[str, Any] | None = None


class HtmlDraftOptions(BaseModel):
    upload_inline_images: bool = True
    force_reupload_cover: bool = False
    create_local_preview: bool = True
    # Re-submit even if this content revision already has a recorded draft or an
    # unknown remote outcome. Only after checking the WeChat draft box.
    force_new: bool = False


class HtmlDraftCreate(BaseModel):
    account_id: int
    title: str = Field(max_length=32)
    html: str = Field(min_length=1)
    asset_base_dir: str | None = None
    author: str | None = None
    digest: str | None = Field(default=None, max_length=128)
    cover_source: str | None = None
    content_source_url: str | None = None
    meta: dict[str, Any] | None = None
    options: HtmlDraftOptions = Field(default_factory=HtmlDraftOptions)

    model_config = {
        "json_schema_extra": {
            "example": {
                "account_id": 1,
                "title": "多 Agent 运行时治理",
                "html": "<section><h1>多 Agent 运行时治理</h1><p>正文</p><img src=\"./assets/diagram.png\"/></section>",
                "asset_base_dir": "/srv/self-media-exchange/inbox/xueyu-gongzhonghao-receive/posts/runtime-governance",
                "author": "木乔",
                "digest": "把已渲染好的微信公众号 HTML 直接送入草稿链路。",
                "cover_source": "./assets/cover.png",
                "content_source_url": "https://example.com/source",
                "meta": {"source_kind": "external_html", "pipeline": "visual-renderer"},
                "options": {
                    "upload_inline_images": True,
                    "force_reupload_cover": False,
                    "create_local_preview": True,
                },
            }
        }
    }


class HtmlDraftFileCreate(BaseModel):
    account_id: int
    html_path: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=32)
    asset_base_dir: str | None = None
    author: str | None = None
    digest: str | None = Field(default=None, max_length=128)
    cover_source: str | None = None
    content_source_url: str | None = None
    meta: dict[str, Any] | None = None
    options: HtmlDraftOptions = Field(default_factory=HtmlDraftOptions)

    model_config = {
        "json_schema_extra": {
            "example": {
                "account_id": 1,
                "html_path": "/srv/self-media-exchange/inbox/xueyu-gongzhonghao-receive/posts/runtime-governance/final.html",
                "title": "多 Agent 运行时治理",
                "cover_source": "./assets/cover.png",
                "meta": {"source_kind": "rendered_html_file"},
                "options": {
                    "upload_inline_images": True,
                    "force_reupload_cover": False,
                    "create_local_preview": True,
                },
            }
        }
    }


class ArticleOut(BaseModel):
    id: int
    account_id: int | None
    title: str
    status: ArticleStatus
    theme: str
    local_preview_path: str | None
    wx_draft_media_id: str | None
    wx_publish_id: str | None
    wx_article_id: str | None
    wx_article_url: str | None
    meta: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RenderRequest(BaseModel):
    theme: str | None = None
    upload_inline_images: bool = False


class DraftRequest(BaseModel):
    upload_inline_images: bool = True
    force_reupload_cover: bool = False
    # Re-submit even if a previous attempt with the same content revision already
    # succeeded or ended with an unknown remote outcome. Only use after checking
    # the WeChat draft box; the default reuses the recorded result instead.
    force_new: bool = False


class BatchDraftRequest(BaseModel):
    article_ids: list[int] = Field(min_length=1, max_length=50)
    upload_inline_images: bool = True
    force_reupload_cover: bool = False
    # Applies to every item: only after checking the WeChat draft box.
    force_new: bool = False


class PreviewRequest(BaseModel):
    # WeChat preview sends a message to the specified user; it does not return a public URL.
    touser_openid: str | None = None
    towxname: str | None = None


class PublishRequest(BaseModel):
    mode: Literal["draft", "preview", "freepublish", "poll", "full_safe"] = "draft"
    preview: PreviewRequest | None = None


class JobOut(BaseModel):
    id: int
    article_id: int
    account_id: int | None
    channel: PublishChannel
    status: JobStatus
    request_json: dict[str, Any] | None
    response_json: dict[str, Any] | None
    error_message: str | None
    error_category: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ThemeMetadataUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    source: str | None = None
    source_url: str | None = None
    preview_cover: str | None = None
    tags: list[str] | None = None


class ThemeImportRequest(ThemeMetadataUpdate):
    name: str
    css: str
    overwrite: bool = False


class ThemeMetadataOut(BaseModel):
    name: str
    display_name: str
    description: str
    source: str
    source_url: str | None
    preview_cover: str | None
    tags: list[str]
    built_in: bool
    created_at: str | None
    updated_at: str | None


class ThemeOut(BaseModel):
    name: str
    css_path: str
    css_size: int
    metadata: ThemeMetadataOut
