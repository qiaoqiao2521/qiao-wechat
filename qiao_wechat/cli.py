from __future__ import annotations

import argparse
import sys

import requests
from pathlib import Path

from .db import db_session, init_db
from .models import Article, PublishJob, WeChatAccount
from .services.publisher import PublishService
from .services.quality_gate import QualityGate
from .services.wechat_repo_flow import WechatRepoFlowService
from .services.xhs_exporter import XhsExporter
from .config import get_settings


def cmd_init(_: argparse.Namespace) -> None:
    init_db()
    print("database initialized")


def cmd_add_account(args: argparse.Namespace) -> None:
    init_db()
    with db_session() as db:
        account = WeChatAccount(
            name=args.name,
            appid=args.appid,
            raw_secret=args.secret,
            secret_env_name=args.secret_env_name,
            account_type=args.account_type,
            is_certified=args.certified,
            author=args.author,
            default_cover_media_id=args.default_cover_media_id,
        )
        db.add(account)
        db.flush()
        print(f"created account id={account.id} name={account.name}")


def cmd_create_article(args: argparse.Namespace) -> None:
    init_db()
    md_path = Path(args.markdown).resolve()
    markdown = md_path.read_text(encoding="utf-8")
    with db_session() as db:
        article = PublishService(db).create_article(
            account_id=args.account_id,
            title=args.title,
            markdown=markdown,
            author=args.author,
            digest=args.digest,
            cover_source=args.cover,
            content_source_url=args.content_source_url,
            theme=args.theme,
        )
        article.meta = {**(article.meta or {}), "source_path": str(md_path)}
        db.flush()
        print(f"created article id={article.id}")


def cmd_list_articles(args: argparse.Namespace) -> None:
    init_db()
    with db_session() as db:
        query = db.query(Article)
        if args.account_id is not None:
            query = query.filter(Article.account_id == args.account_id)
        for article in query.order_by(Article.id.desc()).limit(args.limit).all():
            print(f"{article.id}\t{article.status.value}\taccount={article.account_id or '-'}\t{article.title}")


def cmd_inspect(args: argparse.Namespace) -> None:
    init_db()
    with db_session() as db:
        article = db.get(Article, args.article_id)
        if not article:
            raise SystemExit(f"article not found: {args.article_id}")
        issues = QualityGate().inspect(article.title, article.markdown)
        article.meta = {**(article.meta or {}), "quality_issues": [issue.__dict__ for issue in issues]}
        db.flush()
        if not issues:
            print("no quality issues")
            return
        for issue in issues:
            print(f"[{issue.level}] {issue.message} -> {issue.suggestion}")


def cmd_render(args: argparse.Namespace) -> None:
    init_db()
    with db_session() as db:
        article = PublishService(db).render_article(args.article_id, theme=args.theme, upload_inline_images=args.upload_inline_images)
        print(article.local_preview_path)


def _friendly_draft_error(exc: Exception) -> str:
    """把草稿异常翻译成可执行的下一步，避免操作者去读 traceback。"""
    text = str(exc)
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)) or "unknown remote outcome" in text:
        return (
            "上次创建结果未知（远端超时或连接中断），为避免重复草稿已拦截本次提交。\n"
            "下一步：1) 打开公众号后台「草稿箱」核对是否已有同标题草稿；\n"
            "        2) 确认未创建后，用 --force-new（或 API force_new=true）重新提交。"
        )
    if "cover_source or account.default_cover_media_id" in text:
        return (
            "缺少封面：请为文章设置 cover_source，或给账号配置 default_cover_media_id。\n"
            "可先用 `qiao-wechat render` 生成本地预览确认内容，再补封面提交。"
        )
    if isinstance(exc, FileNotFoundError):
        return f"素材文件不存在：{text}\n下一步：核对图片路径（相对路径按文章/资源基目录解析）。"
    if "WeChat API error" in text:
        return f"微信接口明确拒绝：{text}\n下一步：按 errcode 修正素材/内容后直接重试（未产生草稿）。"
    if "must be under" in text or "content is required" in text:
        return f"内容校验未通过：{text}\n下一步：修改内容后重试（未提交到微信）。"
    return f"草稿创建失败：{text}\n下一步：可用 `qiao-wechat jobs --article-id <id>` 查看已完成的步骤与错误类别。"


def cmd_draft(args: argparse.Namespace) -> None:
    init_db()
    with db_session() as db:
        try:
            article = PublishService(db).create_wechat_draft(
                args.article_id,
                upload_inline_images=not args.no_upload_inline_images,
                force_new=args.force_new,
            )
        except Exception as exc:  # noqa: BLE001 - CLI 边界：转为可执行提示
            print(_friendly_draft_error(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        outcome = (article.meta or {}).get("draft_outcome")
        if outcome == "reused":
            print("本次内容修订已创建过草稿，复用已录得结果（未重复提交远端）。")
        print(f"draft media_id={article.wx_draft_media_id}")


def cmd_html_draft(args: argparse.Namespace) -> None:
    init_db()
    with db_session() as db:
        try:
            article = PublishService(db).create_html_file_draft(
                account_id=args.account_id,
                html_path=args.html,
                title=args.title,
                asset_base_dir=args.asset_base_dir,
                author=args.author,
                digest=args.digest,
                cover_source=args.cover,
                content_source_url=args.content_source_url,
                meta=None,
                upload_inline_images=not args.no_upload_inline_images,
                force_reupload_cover=args.force_reupload_cover,
                create_local_preview=not args.no_local_preview,
                force_new=args.force_new,
            )
        except Exception as exc:  # noqa: BLE001 - CLI 边界：转为可执行提示
            print(_friendly_draft_error(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        outcome = (article.meta or {}).get("draft_outcome")
        if outcome == "reused":
            print("本次内容修订已创建过草稿，复用已录得结果（未重复提交远端）。")
        print(f"article_id={article.id}")
        print(f"draft media_id={article.wx_draft_media_id}")


def _parse_article_ids(raw_values: list[str]) -> list[int]:
    ids: list[int] = []
    for raw in raw_values:
        for part in str(raw).split(","):
            part = part.strip()
            if part:
                ids.append(int(part))
    return ids


def cmd_draft_batch(args: argparse.Namespace) -> None:
    init_db()
    article_ids = _parse_article_ids(args.article_id)
    with db_session() as db:
        summary = PublishService(db).create_wechat_drafts_batch(
            article_ids,
            upload_inline_images=not args.no_upload_inline_images,
            force_reupload_cover=args.force_reupload_cover,
            force_new=args.force_new,
        )
    for item in summary["results"]:
        if item["ok"]:
            label = "reused(复用)" if item["outcome"] == "reused" else "created(新建)"
            print(f"article={item['article_id']}\t{label}\tmedia_id={item['media_id']}")
        else:
            category = item.get("error_category") or ""
            print(f"article={item['article_id']}\tFAILED\t[{category}] {item.get('error', '')[:120]}")
    print(
        f"\n汇总：共 {summary['total']} 篇，新建 {summary['created']}，复用 {summary['reused']}，失败 {summary['failed']}"
    )
    if summary["failed"]:
        print("失败项已留痕，可用 `qiao-wechat jobs --article-id <id>` 查看步骤与错误类别。", file=sys.stderr)
        raise SystemExit(1)


def cmd_preview(args: argparse.Namespace) -> None:
    init_db()
    with db_session() as db:
        payload = PublishService(db).send_preview(args.article_id, touser_openid=args.openid, towxname=args.wxname)
        print(payload)


def cmd_publish(args: argparse.Namespace) -> None:
    init_db()
    with db_session() as db:
        article = PublishService(db).submit_freepublish(args.article_id)
        print(f"publish_id={article.wx_publish_id}")


def cmd_poll(args: argparse.Namespace) -> None:
    init_db()
    with db_session() as db:
        payload = PublishService(db).poll_publish_status(args.article_id)
        print(payload)


def cmd_jobs(args: argparse.Namespace) -> None:
    init_db()
    with db_session() as db:
        query = db.query(PublishJob)
        if args.article_id is not None:
            query = query.filter(PublishJob.article_id == args.article_id)
        for job in query.order_by(PublishJob.id.desc()).limit(args.limit).all():
            err = f" error={job.error_message}" if job.error_message else ""
            category = f" category={job.error_category}" if job.error_category else ""
            print(f"{job.id}\tarticle={job.article_id}\t{job.channel.value}\t{job.status.value}{category}{err}")


def cmd_xhs(args: argparse.Namespace) -> None:
    init_db()
    with db_session() as db:
        path = XhsExporter(db).export_article(args.article_id, tags=args.tags or [])
        print(path)


def cmd_list_drafts(_: argparse.Namespace) -> None:
    init_db()
    with db_session() as db:
        service = WechatRepoFlowService(get_settings(), db)
        items = service.list_drafts()
        for item in items:
            print(f"{item.title}\t{item.chars}\t{item.path}")


def cmd_list_pending(_: argparse.Namespace) -> None:
    init_db()
    with db_session() as db:
        service = WechatRepoFlowService(get_settings(), db)
        items = service.list_pending()
        for item in items:
            print(f"{item.title}\t{item.chars}\t{item.path}")


def cmd_move_draft_to_pending(args: argparse.Namespace) -> None:
    init_db()
    with db_session() as db:
        service = WechatRepoFlowService(get_settings(), db)
        target = service.move_draft_to_pending(args.markdown, copy_only=args.copy_only)
        print(target)


def cmd_publish_pending_draftbox(args: argparse.Namespace) -> None:
    init_db()
    with db_session() as db:
        service = WechatRepoFlowService(get_settings(), db)
        article_id, media_id = service.publish_pending_to_draftbox(
            markdown_path=args.markdown,
            account_id=args.account_id,
            author=args.author,
            digest=args.digest,
            cover=args.cover,
            theme=args.theme,
        )
        print(f"article_id={article_id}")
        print(f"draft media_id={media_id}")


def cmd_archive_pending_after_publish(args: argparse.Namespace) -> None:
    init_db()
    with db_session() as db:
        service = WechatRepoFlowService(get_settings(), db)
        target = service.archive_pending_after_publish(args.markdown)
        print(target)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qiao-wechat")
    sub = p.add_subparsers(required=True)

    s = sub.add_parser("init")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("add-account")
    s.add_argument("--name", required=True)
    s.add_argument("--appid", required=True)
    s.add_argument("--secret")
    s.add_argument("--secret-env-name")
    s.add_argument("--account-type", default="unknown", choices=["subscription", "service", "unknown"])
    s.add_argument("--certified", action="store_true")
    s.add_argument("--author", default="")
    s.add_argument("--default-cover-media-id")
    s.set_defaults(func=cmd_add_account)

    s = sub.add_parser("create-article")
    s.add_argument("--account-id", type=int)
    s.add_argument("--title", required=True)
    s.add_argument("--markdown", required=True)
    s.add_argument("--cover")
    s.add_argument("--author")
    s.add_argument("--digest")
    s.add_argument("--content-source-url")
    s.add_argument("--theme", default="wechat_baseline")
    s.set_defaults(func=cmd_create_article)

    s = sub.add_parser("list-articles")
    s.add_argument("--account-id", type=int)
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_list_articles)

    s = sub.add_parser("inspect")
    s.add_argument("article_id", type=int)
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("render")
    s.add_argument("article_id", type=int)
    s.add_argument("--theme")
    s.add_argument("--upload-inline-images", action="store_true")
    s.set_defaults(func=cmd_render)

    s = sub.add_parser("draft")
    s.add_argument("article_id", type=int)
    s.add_argument("--no-upload-inline-images", action="store_true")
    s.add_argument(
        "--force-new",
        action="store_true",
        help="Re-submit even if this content revision already has a recorded draft or an unknown outcome. Check the draft box first.",
    )
    s.set_defaults(func=cmd_draft)

    s = sub.add_parser("draft-batch", help="批量创建微信草稿（draft-only；单篇失败不中断）")
    s.add_argument("--article-id", action="append", required=True, help="文章 ID，可重复或逗号分隔，如 --article-id 1,2,3")
    s.add_argument("--no-upload-inline-images", action="store_true")
    s.add_argument("--force-reupload-cover", action="store_true")
    s.add_argument("--force-new", action="store_true", help="对所有文章强制重建草稿；先核对草稿箱")
    s.set_defaults(func=cmd_draft_batch)

    s = sub.add_parser("html-draft")
    s.add_argument("--account-id", type=int, required=True)
    s.add_argument("--html", required=True)
    s.add_argument("--title")
    s.add_argument("--asset-base-dir")
    s.add_argument("--cover")
    s.add_argument("--author")
    s.add_argument("--digest")
    s.add_argument("--content-source-url")
    s.add_argument("--no-upload-inline-images", action="store_true")
    s.add_argument("--force-reupload-cover", action="store_true")
    s.add_argument("--no-local-preview", action="store_true")
    s.add_argument(
        "--force-new",
        action="store_true",
        help="Re-submit even if this content revision already has a recorded draft or an unknown outcome. Check the draft box first.",
    )
    s.set_defaults(func=cmd_html_draft)

    s = sub.add_parser("preview")
    s.add_argument("article_id", type=int)
    s.add_argument("--openid")
    s.add_argument("--wxname")
    s.set_defaults(func=cmd_preview)

    s = sub.add_parser("publish")
    s.add_argument("article_id", type=int)
    s.set_defaults(func=cmd_publish)

    s = sub.add_parser("poll")
    s.add_argument("article_id", type=int)
    s.set_defaults(func=cmd_poll)

    s = sub.add_parser("jobs")
    s.add_argument("--article-id", type=int)
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_jobs)

    s = sub.add_parser("xhs-export")
    s.add_argument("article_id", type=int)
    s.add_argument("--tags", nargs="*", default=[])
    s.set_defaults(func=cmd_xhs)

    s = sub.add_parser("list-drafts")
    s.set_defaults(func=cmd_list_drafts)

    s = sub.add_parser("list-pending")
    s.set_defaults(func=cmd_list_pending)

    s = sub.add_parser("move-draft-to-pending")
    s.add_argument("--markdown", required=True)
    s.add_argument("--copy-only", action="store_true")
    s.set_defaults(func=cmd_move_draft_to_pending)

    s = sub.add_parser("publish-pending-draftbox")
    s.add_argument("--markdown", required=True)
    s.add_argument("--account-id", type=int, required=True)
    s.add_argument("--author")
    s.add_argument("--digest")
    s.add_argument("--cover")
    s.add_argument("--theme", default="wechat_baseline")
    s.set_defaults(func=cmd_publish_pending_draftbox)

    s = sub.add_parser("archive-pending-after-publish")
    s.add_argument("--markdown", required=True)
    s.set_defaults(func=cmd_archive_pending_after_publish)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
