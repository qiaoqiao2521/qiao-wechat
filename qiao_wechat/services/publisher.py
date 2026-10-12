from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from ..config import get_settings
from ..limits import MAX_CONTENT_BYTES, MAX_CONTENT_CHARS, normalize_cover_info, validate_draft_text
from ..models import Article, ArticleStatus, JobStatus, MediaAsset, PublishChannel, PublishJob, WeChatAccount
from .html_normalizer import HtmlNormalizer
from .image_service import ImageService
from .obsidian_assets import ObsidianAssetAdapter
from .quality_gate import QualityGate
from .renderer import WeChatMarkdownRenderer
from .wechat_client import WeChatApiClient, WeChatError
from .wechat_export import export_source_links, html_budget, semantic_snapshot, validate_preserved

_LOGGER = logging.getLogger(__name__)

# Failure classifications persisted on PublishJob.error_category. Plain strings:
# no enum CHECK constraint, so old SQLite databases only need ADD COLUMN.
ERROR_CATEGORY_REMOTE_REJECTED = "remote_rejected"
ERROR_CATEGORY_REMOTE_UNKNOWN = "remote_result_unknown"
ERROR_CATEGORY_REMOTE_UNCONFIRMED = "remote_unconfirmed"
ERROR_CATEGORY_VALIDATION = "validation_failed"
ERROR_CATEGORY_LOCAL = "local_error"

_REMOTE_UNKNOWN_HINT = (
    "The request was sent but its remote outcome is unknown (timeout/connection loss). "
    "Check the WeChat Official Account draft box for a draft with this title first; "
    "retry with force_new only after confirming no draft was created."
)

_DRAFT_KEY_LOCKS: dict[str, threading.Lock] = {}
_DRAFT_KEY_LOCKS_GUARD = threading.Lock()


def _draft_lock(request_key: str) -> threading.Lock:
    """Serialize draft submission per request key within this process.

    This suppresses local duplicate execution (API + scheduler in one process).
    Cross-process dedup relies on the persisted request_key, not this lock.
    """
    with _DRAFT_KEY_LOCKS_GUARD:
        lock = _DRAFT_KEY_LOCKS.get(request_key)
        if lock is None:
            lock = threading.Lock()
            _DRAFT_KEY_LOCKS[request_key] = lock
        return lock


class PublishService:
    def __init__(self, session: Session):
        self.session = session
        self.settings = get_settings()
        self.image_service = ImageService(session)
        self.html_normalizer = HtmlNormalizer()
        self.obsidian_assets = ObsidianAssetAdapter()
        self.quality_gate = QualityGate()

    def create_article(self, **kwargs: Any) -> Article:
        article = Article(**kwargs)
        self.session.add(article)
        self.session.flush()
        return article

    def create_html_draft(
        self,
        *,
        account_id: int,
        title: str,
        html: str,
        asset_base_dir: str | None = None,
        author: str | None = None,
        digest: str | None = None,
        cover_source: str | None = None,
        content_source_url: str | None = None,
        meta: dict[str, Any] | None = None,
        upload_inline_images: bool = True,
        force_reupload_cover: bool = False,
        create_local_preview: bool = True,
        force_new: bool = False,
    ) -> Article:
        account = self.session.get(WeChatAccount, account_id)
        if not account:
            raise KeyError(f"wechat account not found: {account_id}")
        if not html.strip():
            raise ValueError("html is required")

        article_meta = {**(meta or {}), "content_ingress": "html"}
        if asset_base_dir:
            article_meta["asset_base_dir"] = str(Path(asset_base_dir).expanduser().resolve())

        article = self.create_article(
            account_id=account_id,
            title=title,
            markdown="",
            html=html,
            author=author,
            digest=digest,
            cover_source=cover_source,
            content_source_url=content_source_url,
            theme="html",
            meta=article_meta,
        )
        try:
            self.prepare_html_article(
                article.id,
                upload_inline_images=upload_inline_images,
                create_local_preview=create_local_preview,
            )
        except Exception as exc:
            self._persist_failure(
                article,
                None,
                exc,
                error_category=self._classify_error(exc, submitted=False),
                meta_patch={"failure_stage": "prepare_html"},
            )
            raise
        return self.create_wechat_draft(
            article.id,
            upload_inline_images=False,
            force_reupload_cover=force_reupload_cover,
            force_new=force_new,
        )

    def create_html_file_draft(
        self,
        *,
        account_id: int,
        html_path: str,
        title: str | None = None,
        asset_base_dir: str | None = None,
        author: str | None = None,
        digest: str | None = None,
        cover_source: str | None = None,
        content_source_url: str | None = None,
        meta: dict[str, Any] | None = None,
        upload_inline_images: bool = True,
        force_reupload_cover: bool = False,
        create_local_preview: bool = True,
        force_new: bool = False,
    ) -> Article:
        path = Path(html_path).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise ValueError(f"html file not found: {path}")

        html = path.read_text(encoding="utf-8")
        final_title = title or path.stem[:32]
        final_asset_base_dir = asset_base_dir or str(path.parent)
        article_meta = {**(meta or {}), "source_html_path": str(path)}

        return self.create_html_draft(
            account_id=account_id,
            title=final_title,
            html=html,
            asset_base_dir=final_asset_base_dir,
            author=author,
            digest=digest,
            cover_source=cover_source,
            content_source_url=content_source_url,
            meta=article_meta,
            upload_inline_images=upload_inline_images,
            force_reupload_cover=force_reupload_cover,
            create_local_preview=create_local_preview,
            force_new=force_new,
        )

    def prepare_html_article(
        self,
        article_id: int,
        *,
        upload_inline_images: bool = True,
        create_local_preview: bool = True,
    ) -> Article:
        article = self._article(article_id)
        if not article.html:
            raise ValueError("article.html is required")
        source_semantics = semantic_snapshot(article.html)
        html = self.html_normalizer.normalize(article.html)
        validate_preserved(source_semantics, html)
        article.html = html
        # Stamp the content revision before media URLs are substituted, so the
        # draft request key stays stable across retries of the same input.
        article.meta = {
            **{key: value for key, value in (article.meta or {}).items()
               if key not in {"wechat_source_links", "wechat_export_preservation", "wechat_html_budget"}},
            "content_revision": self._compute_content_revision(article),
            "wechat_source_semantics": source_semantics,
            "draft_metadata_revision": self._compute_draft_metadata_revision(article),
        }
        if upload_inline_images:
            account = self._require_account(article)
            client = WeChatApiClient(self.session, account)
            html = WeChatMarkdownRenderer().replace_image_sources(
                html,
                lambda src: self._upload_inline(account, client, src, base_dir=self._resolve_asset_base_dir(article)),
            )
            article.html = html
        if create_local_preview:
            out = self.settings.output_dir / f"article_{article.id}" / "wechat_preview.html"
            preview_html = self._materialize_preview_images(html, article)
            WeChatMarkdownRenderer().save_preview(preview_html, out, page_title=article.title)
            article.local_preview_path = str(out)
            article.meta = {**(article.meta or {}), "local_preview_url": f"{self.settings.app_base_url}/preview/article/{article.id}"}
            self._job(article, PublishChannel.local_preview, JobStatus.success, response={"path": str(out)})
        article.status = ArticleStatus.rendered
        self.session.flush()
        return article

    def render_article(self, article_id: int, *, theme: str | None = None, upload_inline_images: bool = False) -> Article:
        article = self._article(article_id)
        theme_name = theme or article.theme or self.settings.render_theme
        renderer = WeChatMarkdownRenderer(theme_name=theme_name)
        markdown_dir = self._resolve_markdown_dir(article)
        prepared_markdown = self.obsidian_assets.rewrite_image_embeds(article.markdown, base_dir=markdown_dir)
        html = renderer.render(prepared_markdown, title=article.title)
        source_semantics = semantic_snapshot(html)
        if upload_inline_images:
            account = self._require_account(article)
            client = WeChatApiClient(self.session, account)
            html = renderer.replace_image_sources(html, lambda src: self._upload_inline(account, client, src, base_dir=markdown_dir))
        out = self.settings.output_dir / f"article_{article.id}" / "wechat_preview.html"
        preview_html = self._materialize_preview_images(html, article, base_dir=markdown_dir)
        renderer.save_preview(preview_html, out, page_title=article.title)
        # Stamp the revision from the raw markdown BEFORE article.html is overwritten
        # with rendered output, so the value matches what _draft_request_key computes
        # on the first call (before any render has happened).
        content_revision = self._compute_content_revision(article, theme=theme_name)
        article.html = html
        article.theme = theme_name
        article.local_preview_path = str(out)
        article.status = ArticleStatus.rendered
        article.meta = {
            **{key: value for key, value in (article.meta or {}).items()
               if key not in {"wechat_source_links", "wechat_export_preservation", "wechat_html_budget"}},
            "local_preview_url": f"{self.settings.app_base_url}/preview/article/{article.id}",
            "content_revision": content_revision,
            "wechat_source_semantics": source_semantics,
            "draft_metadata_revision": self._compute_draft_metadata_revision(article),
        }
        self._job(article, PublishChannel.local_preview, JobStatus.success, response={"path": str(out)})
        self.session.flush()
        return article

    def create_wechat_draft(
        self,
        article_id: int,
        *,
        upload_inline_images: bool = True,
        force_reupload_cover: bool = False,
        force_new: bool = False,
    ) -> Article:
        article = self._article(article_id)
        account = self._require_account(article)
        # Validation must precede duplicate reuse as well as any remote uploads.
        # Otherwise an invalid edited digest could reuse an old successful job.
        old_unknown_key = (article.meta or {}).get("draft_request_key")
        if not force_new and old_unknown_key and (article.meta or {}).get("draft_outcome") == "unknown":
            self._raise_if_outcome_unknown(account.id, old_unknown_key)
        try:
            validate_draft_text(article.title, article.digest)
            request_key = self._draft_request_key(article, account)
        except ValueError as exc:
            self._persist_failure(article, None, exc, error_category=ERROR_CATEGORY_VALIDATION,
                                  meta_patch={"draft_outcome": "failed", "draft_failure_stage": "prepare"})
            raise
        with _draft_lock(request_key):
            return self._create_wechat_draft_locked(
                article,
                account,
                request_key,
                upload_inline_images=upload_inline_images,
                force_reupload_cover=force_reupload_cover,
                force_new=force_new,
            )

    def _create_wechat_draft_locked(
        self,
        article: Article,
        account: WeChatAccount,
        request_key: str,
        *,
        upload_inline_images: bool,
        force_reupload_cover: bool,
        force_new: bool,
    ) -> Article:
        client = WeChatApiClient(self.session, account)
        if not force_new:
            # Crop-aware keys must not bypass uncertain submissions made by the
            # older key algorithm. Keep its existing reconciliation guard.
            legacy_key = self._legacy_draft_request_key(article, account)
            if legacy_key != request_key:
                self._raise_if_outcome_unknown(account.id, legacy_key)
            reused = self._try_reuse_previous_draft(article, account.id, request_key)
            if reused is not None:
                return reused
            self._raise_if_outcome_unknown(account.id, request_key)

        if not article.html or upload_inline_images:
            self.render_article(article.id, upload_inline_images=upload_inline_images)
            self.session.flush()
            article = self._article(article.id)

        job = self._job(
            article,
            PublishChannel.wx_draft,
            JobStatus.running,
            request={"title": article.title, "request_key": request_key},
        )
        job.request_key = request_key
        self.session.flush()
        submitted = False
        try:
            digest = article.digest if article.digest is not None else self._auto_digest(article.markdown or article.html or "")
            validate_draft_text(article.title, digest)
            cover_info = normalize_cover_info((article.meta or {}).get("cover_info"))
            article.html = self._externalize_inline_svgs(article, account, client, article.html)
            cover_media_id = self._ensure_cover_media_id(article, account, client, force_reupload=force_reupload_cover)
            wx_article = {
                "article_type": "news",
                "title": article.title,
                "thumb_media_id": cover_media_id,
                "author": (article.author or account.author or self.settings.default_author or "")[:16],
                "digest": digest,
                "content": self._prepare_wechat_draft_content(article, article.html),
                "content_source_url": (article.content_source_url or "")[:1024],
                "need_open_comment": 0,
                "only_fans_can_comment": 0,
            }
            if cover_info is not None:
                wx_article["cover_info"] = cover_info
            self._validate_rendered_publish_artifacts(article.title, wx_article["content"])
            self._validate_draft_content(wx_article["content"])
            job.request_json = {"request_key": request_key, "articles": [{**wx_article, "content": "<omitted>"}]}
            submitted = True
            payload = client.add_draft([wx_article])
            media_id = payload["media_id"]
            article.wx_draft_media_id = media_id
            article.status = ArticleStatus.draft_created
            job.status = JobStatus.success
            job.response_json = payload
            job.finished_at = _utcnow_naive()
            article.meta = {**(article.meta or {}), "draft_outcome": "created", "draft_request_key": request_key}
        except Exception as exc:
            category = self._classify_error(exc, submitted=submitted)
            meta_patch = {
                "draft_outcome": "unknown" if category == ERROR_CATEGORY_REMOTE_UNKNOWN else "failed",
                "draft_request_key": request_key,
                "draft_failure_stage": "submit" if submitted else "prepare",
            }
            if category == ERROR_CATEGORY_REMOTE_UNKNOWN:
                meta_patch["draft_reconcile_hint"] = _REMOTE_UNKNOWN_HINT
            self._persist_failure(article, job, exc, error_category=category, meta_patch=meta_patch)
            raise
        # Success path: persist the remote outcome before returning. A commit failure
        # here means the draft exists remotely but was not recorded — an unknown
        # result, handled separately so it cannot be overwritten by the submit
        # failure path above.
        article_snapshot = {
            "id": article.id,
            "account_id": article.account_id,
            "title": article.title,
            "markdown": article.markdown or "",
        }
        job_snapshot = {"id": job.id, "channel": job.channel}
        try:
            self.session.commit()
        except Exception as commit_exc:
            self.session.rollback()
            self._persist_unknown_after_commit_failure(article_snapshot, job_snapshot, request_key, commit_exc)
            raise
        return article

    def create_wechat_drafts_batch(
        self,
        article_ids: list[int],
        *,
        upload_inline_images: bool = True,
        force_reupload_cover: bool = False,
        force_new: bool = False,
    ) -> dict[str, Any]:
        """Create WeChat drafts for multiple articles, draft-only, best-effort.

        One article's failure never aborts the rest. Each item goes through the same
        idempotent create_wechat_draft path, so duplicates reuse recorded results.
        Returns a summary; callers surface it as-is (CLI/API), no partial progress
        faking: every entry reflects the real per-article outcome.
        """
        results: list[dict[str, Any]] = []
        for article_id in article_ids:
            try:
                article = self.create_wechat_draft(
                    article_id,
                    upload_inline_images=upload_inline_images,
                    force_reupload_cover=force_reupload_cover,
                    force_new=force_new,
                )
                outcome = (article.meta or {}).get("draft_outcome", "created")
                results.append(
                    {
                        "article_id": article_id,
                        "ok": True,
                        "outcome": outcome,
                        "media_id": article.wx_draft_media_id,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - 批量语义：单篇失败不中断
                # 失败明细与 error_category 已由 create_wechat_draft 持久化到任务记录；
                # 这里只做展示用分类。
                category = self._classify_error(exc, submitted=True)
                results.append(
                    {
                        "article_id": article_id,
                        "ok": False,
                        "outcome": "unknown" if category == ERROR_CATEGORY_REMOTE_UNKNOWN else "failed",
                        "media_id": None,
                        "error": str(exc),
                        "error_category": category,
                    }
                )
        return {
            "results": results,
            "total": len(results),
            "created": sum(1 for r in results if r["ok"] and r["outcome"] == "created"),
            "reused": sum(1 for r in results if r["ok"] and r["outcome"] == "reused"),
            "failed": sum(1 for r in results if not r["ok"]),
        }

    def send_preview(self, article_id: int, *, touser_openid: str | None = None, towxname: str | None = None) -> dict[str, Any]:
        self._require_preview_enabled()
        article = self._article(article_id)
        account = self._require_account(article)
        if not article.wx_draft_media_id:
            self.create_wechat_draft(article_id)
            self.session.flush()
            article = self._article(article_id)
        client = WeChatApiClient(self.session, account)
        job = self._job(article, PublishChannel.wx_preview, JobStatus.running, request={"touser": bool(touser_openid), "towxname": towxname})
        try:
            payload = client.preview_mpnews(article.wx_draft_media_id, touser_openid=touser_openid, towxname=towxname)
            article.status = ArticleStatus.preview_sent
            job.status = JobStatus.success
            job.response_json = payload
            job.finished_at = _utcnow_naive()
            self.session.flush()
            return payload
        except Exception as exc:
            self._persist_failure(
                article,
                job,
                exc,
                error_category=self._classify_error(exc, submitted=True),
                mark_article_failed=False,
            )
            raise

    def submit_freepublish(self, article_id: int) -> Article:
        self._require_publish_enabled()
        article = self._article(article_id)
        account = self._require_account(article)
        if not article.wx_draft_media_id:
            self.create_wechat_draft(article_id)
            self.session.flush()
            article = self._article(article_id)
        client = WeChatApiClient(self.session, account)
        job = self._job(article, PublishChannel.wx_freepublish, JobStatus.running, request={"media_id": article.wx_draft_media_id})
        try:
            payload = client.submit_freepublish(article.wx_draft_media_id)
            article.wx_publish_id = payload.get("publish_id")
            if not article.wx_publish_id:
                raise ValueError("WeChat freepublish response did not return publish_id")
            article.status = ArticleStatus.publish_submitted
            job.status = JobStatus.success
            job.response_json = payload
            job.finished_at = _utcnow_naive()
        except Exception as exc:
            self._persist_failure(
                article,
                job,
                exc,
                error_category=self._classify_error(exc, submitted=True),
                meta_patch={"failure_stage": "freepublish_submit"},
            )
            raise
        self.session.flush()
        return article

    def poll_publish_status(self, article_id: int) -> dict[str, Any]:
        article = self._article(article_id)
        account = self._require_account(article)
        if not article.wx_publish_id:
            raise ValueError("article has no wx_publish_id")
        client = WeChatApiClient(self.session, account)
        payload = client.get_publish_status(article.wx_publish_id)
        status = payload.get("publish_status")
        article_id_from_wx = payload.get("article_id") or payload.get("article_detail", {}).get("article_id")
        if article_id_from_wx:
            article.wx_article_id = str(article_id_from_wx)
        article_url = self._article_url_from_publish_status(payload)
        if article_url:
            article.wx_article_url = article_url
        normalized = str(status)
        if normalized == "0" or (status is None and article_id_from_wx):
            article.status = ArticleStatus.published
            if not article.wx_article_url:
                self._hydrate_published_url(article, client)
        elif normalized in {"2", "3", "4", "5", "6"}:
            article.status = ArticleStatus.failed
        self._job(
            article,
            PublishChannel.wx_freepublish,
            JobStatus.failed if article.status == ArticleStatus.failed else JobStatus.success,
            response=payload,
        )
        self.session.flush()
        return payload

    def _hydrate_published_url(self, article: Article, client: WeChatApiClient) -> None:
        if not article.wx_article_id:
            return
        try:
            detail = client.get_published_article(article.wx_article_id)
        except Exception:
            return
        # Common shapes: {'news_item': [{'url': '...'}]} or nested article_detail.
        url = None
        if isinstance(detail.get("news_item"), list) and detail["news_item"]:
            url = detail["news_item"][0].get("article_url")
        if not url:
            url = detail.get("article_url")
        if url:
            article.wx_article_url = url

    def _ensure_cover_media_id(self, article: Article, account: WeChatAccount, client: WeChatApiClient, *, force_reupload: bool) -> str:
        if article.cover_media_id and not force_reupload:
            return article.cover_media_id
        if account.default_cover_media_id and not article.cover_source and not force_reupload:
            article.cover_media_id = account.default_cover_media_id
            return account.default_cover_media_id
        if not article.cover_source:
            raise ValueError("cover_source or account.default_cover_media_id is required for WeChat draft")
        cover_source = self._resolve_image_source(article, article.cover_source)
        asset, blob = self.image_service.prepare_asset_for_wechat(account_id=account.id, source=cover_source, purpose="cover")
        if asset.media_id and not force_reupload:
            article.cover_media_id = asset.media_id
            return asset.media_id
        payload = client.upload_permanent_image(blob.path)
        asset.media_id = payload.get("media_id")
        asset.wx_url = payload.get("url")
        asset.raw_response = payload
        if not asset.media_id:
            raise ValueError("WeChat cover upload did not return media_id")
        article.cover_media_id = asset.media_id
        return asset.media_id

    def _upload_inline(self, account: WeChatAccount, client: WeChatApiClient, src: str, *, base_dir: Path | None = None) -> str:
        from urllib.parse import unquote
        decoded = unquote(src)
        resolved = decoded
        if base_dir and not decoded.startswith(("http://", "https://", "/")):
            candidate = (base_dir / Path(decoded)).resolve()
            if candidate.exists():
                resolved = str(candidate)
        asset, blob = self.image_service.prepare_asset_for_wechat(account_id=account.id, source=resolved, purpose="inline")
        if asset.wx_url:
            return asset.wx_url
        payload = client.upload_inline_image(blob.path)
        asset.wx_url = payload.get("url")
        asset.raw_response = payload
        if not asset.wx_url:
            raise ValueError("WeChat inline image upload did not return url")
        self.session.flush()
        return asset.wx_url

    @staticmethod
    def _auto_digest(markdown: str) -> str:
        if "<" in markdown and ">" in markdown:
            html_text = BeautifulSoup(markdown, "html.parser").get_text(" ")
            html_text = " ".join(html_text.split())
            if html_text:
                return html_text[:100]
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
        text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
        text = re.sub(r"[*_`>~-]", "", text)
        text = BeautifulSoup(text, "html.parser").get_text(" ")
        text = " ".join(text.split())
        if not text:
            text = markdown.replace("#", "").strip()
        return text[:100]

    @staticmethod
    def _validate_draft_content(content: str | None) -> None:
        if not content:
            raise ValueError("WeChat draft content is required")
        if len(content) >= MAX_CONTENT_CHARS or len(content.encode("utf-8")) >= MAX_CONTENT_BYTES:
            budget = html_budget(content)
            raise ValueError(
                "WeChat draft content must be under 20000 characters and under 1MB; "
                f"chars={budget['chars']}, utf8_bytes={budget['utf8_bytes']}, "
                f"remaining_chars={budget['remaining_chars']}, remaining_utf8_bytes={budget['remaining_utf8_bytes']}, "
                f"parts_chars={budget['parts_chars']}, parts_utf8_bytes={budget['parts_utf8_bytes']}. "
                "Split the article or link to a full version; text was not truncated."
            )

    def _prepare_wechat_draft_content(self, article: Article, content: str | None) -> str:
        if not content:
            self._validate_draft_content(content)
            return content or ""

        original_chars = len(content)
        original_bytes = len(content.encode("utf-8"))
        source_semantics = (article.meta or {}).get("wechat_source_semantics") or semantic_snapshot(content)
        optimized = self.html_normalizer.normalize(content)
        optimized = self._normalize_wechat_ordered_lists(optimized)
        optimized, links = export_source_links(optimized)
        candidate = self._compact_wechat_html(optimized)
        if len(candidate) < len(optimized) or len(candidate.encode("utf-8")) < len(optimized.encode("utf-8")):
            optimized = candidate
        preservation = validate_preserved(source_semantics, optimized)

        meta = {**(article.meta or {})}
        meta["wechat_draft_compaction"] = {
            "applied": optimized != content,
            "original_chars": original_chars,
            "final_chars": len(optimized),
            "original_bytes": original_bytes,
            "final_bytes": len(optimized.encode("utf-8")),
        }
        meta["wechat_export_preservation"] = preservation
        previous_links = meta.get("wechat_source_links") or {}
        meta["wechat_source_semantics"] = source_semantics
        meta["wechat_source_links"] = {
            "external_urls": list(dict.fromkeys([*previous_links.get("external_urls", []), *links["external_urls"]])),
            "footnotes_added": previous_links.get("footnotes_added", 0) + links["footnotes_added"],
            "unsafe_links_removed": previous_links.get("unsafe_links_removed", 0) + links["unsafe_links_removed"],
        }
        meta["wechat_html_budget"] = html_budget(optimized)
        article.meta = meta

        self._validate_draft_content(optimized)
        return optimized

    def _validate_rendered_publish_artifacts(self, title: str, content: str) -> None:
        issues = self.quality_gate.inspect(title, content)
        blocking = [issue for issue in issues if "残留了" in issue.message and issue.level in {"medium", "high"}]
        if not blocking:
            return
        summary = "；".join(f"{issue.message} -> {issue.suggestion}" for issue in blocking)
        raise ValueError(f"publish blocked by rendered artifact gate: {summary}")

    def _externalize_inline_svgs(
        self,
        article: Article,
        account: WeChatAccount,
        client: WeChatApiClient,
        content: str | None,
    ) -> str:
        if not content or "<svg" not in content.lower():
            return content or ""

        soup = BeautifulSoup(content, "html.parser")
        svg_nodes = soup.find_all("svg")
        if not svg_nodes:
            return content

        exported_dir = self.settings.upload_dir / f"article_{article.id}" / "inline-svg"
        exported_dir.mkdir(parents=True, exist_ok=True)

        for index, svg in enumerate(svg_nodes, start=1):
            svg_path = exported_dir / f"inline-svg-{index}.svg"
            svg_path.write_text(str(svg), encoding="utf-8")

            replacement = soup.new_tag("img")
            replacement["src"] = self._upload_inline(account, client, str(svg_path))
            replacement["data-original-src"] = str(svg_path)
            replacement["alt"] = svg.get("aria-label") or svg.get("alt") or f"inline-svg-{index}"

            style = svg.get("style")
            if style:
                replacement["style"] = style

            width = svg.get("width")
            height = svg.get("height")
            if width:
                replacement["width"] = width
            if height:
                replacement["height"] = height

            svg.replace_with(replacement)

        return str(soup)

    @staticmethod
    def _compact_wechat_html(content: str) -> str:
        return HtmlNormalizer().compact(content)

    @staticmethod
    def _normalize_wechat_ordered_lists(content: str) -> str:
        soup = BeautifulSoup(content, "html.parser")
        changed = False

        list_specs = (
            ("ol", lambda index: f"{index}、"),
            ("ul", lambda index: "• "),
        )

        for list_name, prefix_builder in list_specs:
            for list_node in soup.find_all(list_name):
                items = list_node.find_all("li", recursive=False)
                if not items:
                    continue

                paragraph = soup.new_tag("p")
                for index, item in enumerate(items, start=1):
                    if index > 1:
                        paragraph.append(soup.new_tag("br"))
                    marker = soup.new_tag("span")
                    marker["data-wechat-list-marker"] = "1"
                    marker.string = prefix_builder(index)
                    paragraph.append(marker)

                    for child in list(item.contents):
                        child_name = getattr(child, "name", None)
                        if child_name in {"p", "div"}:
                            first_nested = True
                            for nested_child in list(child.contents):
                                if not first_nested:
                                    paragraph.append(soup.new_tag("br"))
                                paragraph.append(nested_child.extract())
                                first_nested = False
                            child.extract()
                            continue
                        paragraph.append(child.extract())

                list_node.insert_before(paragraph)

                list_node.decompose()
                changed = True

        if not changed:
            return content
        return str(soup)

    @staticmethod
    def _article_url_from_publish_status(payload: dict[str, Any]) -> str | None:
        detail = payload.get("article_detail")
        if not isinstance(detail, dict):
            return None
        items = detail.get("item")
        if isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, dict):
                return first.get("article_url")
        return None

    def _article(self, article_id: int) -> Article:
        article = self.session.get(Article, article_id)
        if not article:
            raise KeyError(f"article not found: {article_id}")
        return article

    def _require_account(self, article: Article) -> WeChatAccount:
        if not article.account_id:
            raise ValueError("article.account_id is required for WeChat publishing")
        account = self.session.get(WeChatAccount, article.account_id)
        if not account:
            raise KeyError(f"wechat account not found: {article.account_id}")
        return account

    # --- draft idempotency and failure persistence -------------------------------

    def _materialize_preview_images(self, html: str, article: Article, *, base_dir: Path | None = None) -> str:
        """Copy locally referenced images next to the preview file and rewrite srcs.

        Relative image paths cannot resolve from the preview output directory, so the
        local preview would show broken images even though draft upload handles them
        later. Only the preview file is rewritten; article.html is left untouched.
        """
        soup = BeautifulSoup(html, "html.parser")
        changed = False
        assets_dir = None
        for img in soup.find_all("img"):
            src = img.get("src")
            if not src or src.startswith(("http://", "https://", "data:", "mock:")):
                continue
            source = self._resolve_preview_image_source(src, base_dir)
            if source is None:
                continue
            if assets_dir is None:
                assets_dir = self.settings.output_dir / f"article_{article.id}" / "assets"
                assets_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
            target = assets_dir / f"{digest}{source.suffix.lower()}"
            if not target.exists():
                shutil.copy2(source, target)
            img["src"] = f"assets/{target.name}"
            changed = True
        if not changed:
            return html
        return str(soup)

    def _resolve_preview_image_source(self, src: str, base_dir: Path | None) -> Path | None:
        from urllib.parse import unquote

        decoded = unquote(src.split("?", 1)[0].split("#", 1)[0])
        candidates = []
        if decoded.startswith("/"):
            candidates.append(Path(decoded))
        elif base_dir is not None:
            candidates.append(base_dir / decoded)
        else:
            candidates.append(Path(decoded))
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved.is_file():
                return resolved
        return None

    @staticmethod
    def _compute_content_revision(article: Article, *, theme: str | None = None) -> str:
        """Stable hash over the inputs that determine the produced draft.

        Computed from the raw markdown (or pre-substitution normalized HTML) plus
        draft metadata, so retries of the same input hash the same while real
        content or theme changes produce a new revision.
        """
        payload = {
            "account_id": article.account_id,
            "ingress": (article.meta or {}).get("content_ingress", "markdown"),
            "title": article.title,
            "author": article.author or "",
            "digest": article.digest or "",
            "cover_source": article.cover_source or "",
            "content_source_url": article.content_source_url or "",
            "theme": theme if theme is not None else (article.theme or ""),
            "content": article.markdown or article.html or "",
        }
        cover_info = normalize_cover_info((article.meta or {}).get("cover_info"))
        if cover_info is not None:
            payload["cover_info"] = cover_info
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _draft_request_key(self, article: Article, account: WeChatAccount) -> str:
        approved_metadata = (article.meta or {}).get("draft_metadata_revision")
        if approved_metadata and approved_metadata != self._compute_draft_metadata_revision(article):
            raise ValueError("draft metadata changed after preparing the approved source; run prepare_html_article or render_article for the new version before submitting")
        revision = (article.meta or {}).get("content_revision")
        if not revision:
            revision = self._compute_content_revision(article)
        cover_info = normalize_cover_info((article.meta or {}).get("cover_info"))
        crop_revision = ":" + json.dumps(cover_info, sort_keys=True) if cover_info is not None else ""
        return hashlib.sha256(f"{account.id}:wx_draft:{revision}{crop_revision}".encode("utf-8")).hexdigest()

    @staticmethod
    def _compute_draft_metadata_revision(article: Article) -> str:
        payload = {name: getattr(article, name) or "" for name in (
            "title", "author", "digest", "cover_source", "content_source_url", "theme"
        )}
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def _legacy_draft_request_key(self, article: Article, account: WeChatAccount) -> str:
        revision = (article.meta or {}).get("content_revision") or self._compute_content_revision(article)
        return hashlib.sha256(f"{account.id}:wx_draft:{revision}".encode("utf-8")).hexdigest()

    def _find_previous_draft_job(self, account_id: int, request_key: str, *, status: JobStatus) -> PublishJob | None:
        return (
            self.session.query(PublishJob)
            .filter(
                PublishJob.channel == PublishChannel.wx_draft,
                PublishJob.account_id == account_id,
                PublishJob.request_key == request_key,
                PublishJob.status == status,
            )
            .order_by(PublishJob.id.desc())
            .first()
        )

    def _try_reuse_previous_draft(self, article: Article, account_id: int, request_key: str) -> Article | None:
        previous = self._find_previous_draft_job(account_id, request_key, status=JobStatus.success)
        if previous is None:
            return None
        media_id = (previous.response_json or {}).get("media_id")
        if not media_id:
            return None
        meta = article.meta or {}
        if not meta.get("draft_metadata_revision") and meta.get("content_revision"):
            # Old HTML may already contain substituted image URLs, so its raw
            # revision cannot always be recovered. Reject uncertain legacy reuse
            # rather than treating mutable metadata as a proven old version.
            if meta["content_revision"] != self._compute_content_revision(article):
                raise ValueError("legacy draft metadata cannot be matched to its prepared source; run prepare_html_article or render_article for the approved version before retrying")
        article.wx_draft_media_id = media_id
        article.status = ArticleStatus.draft_created
        article.meta = {
            **(article.meta or {}),
            "draft_outcome": "reused",
            "draft_request_key": request_key,
            "reused_from_job_id": previous.id,
        }
        reuse_job = self._job(
            article,
            PublishChannel.wx_draft,
            JobStatus.success,
            request={"request_key": request_key, "reused_job_id": previous.id},
            response={"media_id": media_id, "reused": True},
        )
        reuse_job.request_key = request_key
        self.session.commit()
        return article

    def _raise_if_outcome_unknown(self, account_id: int, request_key: str) -> None:
        previous = self._find_previous_draft_job(account_id, request_key, status=JobStatus.failed)
        if previous is not None and previous.error_category == ERROR_CATEGORY_REMOTE_UNKNOWN:
            raise ValueError(
                f"previous draft attempt (job #{previous.id}) has an unknown remote outcome; "
                f"a draft with this content may already exist in the WeChat draft box. "
                f"{_REMOTE_UNKNOWN_HINT}"
            )

    @staticmethod
    def _classify_error(exc: Exception, *, submitted: bool) -> str:
        if isinstance(exc, WeChatError):
            return ERROR_CATEGORY_REMOTE_REJECTED
        if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
            # Once the request was sent the remote outcome cannot be known locally;
            # before that, only local state needs cleanup (retrying is safe).
            return ERROR_CATEGORY_REMOTE_UNKNOWN if submitted else ERROR_CATEGORY_REMOTE_UNCONFIRMED
        if isinstance(exc, ValueError):
            return ERROR_CATEGORY_VALIDATION
        return ERROR_CATEGORY_LOCAL

    def _persist_failure(
        self,
        article: Article,
        job: PublishJob | None,
        exc: Exception,
        *,
        error_category: str,
        mark_article_failed: bool = True,
        meta_patch: dict[str, Any] | None = None,
    ) -> None:
        """Make the failure record durable before the caller re-raises.

        API get_db / db_session roll back after an exception, which erases the
        attempt entirely (the article row included, for fresh articles) — verified
        by regression. Commit the failure state here so operators can query what
        happened in a new session. If the database itself refuses the commit,
        retry once on a fresh session bound to the same engine.
        """
        snapshot = {
            "id": article.id,
            "account_id": article.account_id,
            "title": article.title,
            "markdown": article.markdown or "",
        }
        job_snapshot = {"id": job.id if job is not None else None, "channel": job.channel if job is not None else None}
        if mark_article_failed:
            article.status = ArticleStatus.failed
        if meta_patch:
            article.meta = {**(article.meta or {}), **meta_patch}
        if job is not None:
            job.status = JobStatus.failed
            job.error_message = str(exc)
            job.error_category = error_category
            job.finished_at = _utcnow_naive()
            if isinstance(exc, WeChatError):
                job.response_json = exc.payload
        try:
            self.session.commit()
            return
        except Exception:
            self.session.rollback()
        self._persist_failure_fresh_session(
            snapshot,
            job_snapshot,
            exc,
            error_category=error_category,
            mark_article_failed=mark_article_failed,
            meta_patch=meta_patch,
            request_key=(meta_patch or {}).get("draft_request_key"),
        )

    def _persist_failure_fresh_session(
        self,
        snapshot: dict[str, Any],
        job_snapshot: dict[str, Any],
        exc: Exception,
        *,
        error_category: str,
        mark_article_failed: bool,
        meta_patch: dict[str, Any] | None,
        request_key: str | None = None,
    ) -> None:
        try:
            with Session(self.session.get_bind(), future=True) as retry_session:
                retry_article = retry_session.get(Article, snapshot["id"]) if snapshot["id"] is not None else None
                if retry_article is None:
                    retry_article = Article(
                        account_id=snapshot["account_id"],
                        title=snapshot["title"],
                        markdown=snapshot["markdown"],
                        meta={"recovered_failure_record": True},
                    )
                    retry_session.add(retry_article)
                    retry_session.flush()
                if mark_article_failed:
                    retry_article.status = ArticleStatus.failed
                if meta_patch:
                    retry_article.meta = {**(retry_article.meta or {}), **meta_patch}
                retry_job = retry_session.get(PublishJob, job_snapshot["id"]) if job_snapshot["id"] is not None else None
                if retry_job is None and job_snapshot["channel"] is not None:
                    retry_job = PublishJob(
                        article_id=retry_article.id,
                        account_id=snapshot["account_id"],
                        channel=job_snapshot["channel"],
                        status=JobStatus.failed,
                    )
                    retry_session.add(retry_job)
                    retry_session.flush()
                if retry_job is not None:
                    retry_job.status = JobStatus.failed
                    retry_job.error_message = str(exc)
                    retry_job.error_category = error_category
                    retry_job.finished_at = _utcnow_naive()
                    if request_key:
                        # The dedup guard and unknown-outcome guard query this column;
                        # a recovered job without it would let a blind retry through.
                        retry_job.request_key = request_key
                    if isinstance(exc, WeChatError):
                        retry_job.response_json = exc.payload
                retry_session.commit()
        except Exception:
            _LOGGER.warning(
                "could not persist failure record for article %s (%s)",
                snapshot.get("id"),
                error_category,
                exc_info=True,
            )

    def _persist_unknown_after_commit_failure(
        self,
        article_snapshot: dict[str, Any],
        job_snapshot: dict[str, Any],
        request_key: str,
        commit_exc: Exception,
    ) -> None:
        """The remote draft call succeeded but the success state could not commit.

        Record the outcome as unknown (fresh session) so the operator reconciles
        with the draft box instead of blindly re-submitting and duplicating it.
        """
        message = f"draft likely created remotely but local persistence failed: {commit_exc}"
        self._persist_failure_fresh_session(
            article_snapshot,
            job_snapshot,
            RuntimeError(message),
            error_category=ERROR_CATEGORY_REMOTE_UNKNOWN,
            mark_article_failed=True,
            meta_patch={
                "draft_outcome": "unknown",
                "draft_request_key": request_key,
                "draft_failure_stage": "submit",
                "draft_reconcile_hint": _REMOTE_UNKNOWN_HINT,
            },
            request_key=request_key,
        )

    # --- end draft idempotency helpers -------------------------------------------

    @staticmethod
    def _resolve_markdown_dir(article: Article) -> Path | None:
        meta = article.meta or {}
        source = meta.get("source_path")
        if source:
            p = Path(source)
            if p.is_file():
                return p.parent
        return None

    @staticmethod
    def _resolve_asset_base_dir(article: Article) -> Path | None:
        meta = article.meta or {}
        source = meta.get("asset_base_dir")
        if source:
            return Path(source)
        return PublishService._resolve_markdown_dir(article)

    @staticmethod
    def _resolve_image_source(article: Article, source: str) -> str:
        if source.startswith(("http://", "https://", "/")):
            return source
        base_dir = PublishService._resolve_asset_base_dir(article)
        if not base_dir:
            return source
        candidate = (base_dir / Path(source)).resolve()
        if candidate.exists():
            return str(candidate)
        return source

    def _require_preview_enabled(self) -> None:
        if self.settings.allow_wechat_preview:
            return
        raise ValueError(
            "WeChat phone preview is disabled. "
            "Set WCM_ALLOW_WECHAT_PREVIEW=true only after confirming the account has message/mass/preview permission. "
            "Personal unverified accounts should stay in local-preview + draftbox mode."
        )

    def _require_publish_enabled(self) -> None:
        if self.settings.allow_wechat_publish:
            return
        raise ValueError(
            "WeChat freepublish submit is disabled. "
            "Set WCM_ALLOW_WECHAT_PUBLISH=true only after confirming the account has the required publish permission. "
            "Personal unverified accounts should stay in draftbox mode."
        )

    def _job(
        self,
        article: Article,
        channel: PublishChannel,
        status: JobStatus,
        *,
        request: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
    ) -> PublishJob:
        job = PublishJob(
            article_id=article.id,
            account_id=article.account_id,
            channel=channel,
            status=status,
            request_json=request,
            response_json=response,
            started_at=_utcnow_naive() if status == JobStatus.running else None,
            finished_at=_utcnow_naive() if status in (JobStatus.success, JobStatus.failed) else None,
        )
        self.session.add(job)
        self.session.flush()
        return job


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
