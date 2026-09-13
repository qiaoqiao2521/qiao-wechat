#!/usr/bin/env python3
"""qiao-wechat 排版与草稿可靠性样本回归（可重复执行，无需真实凭据/微信接口）。

内容：
1. 用 examples/regression/sample_full.md 在代表性主题上渲染并做结构检查；
2. 生成 375/430px 预览文件（人工抽查用，浏览器截图验证记录见 plans/ 任务进展）；
3. 走 HTML 入口（字符串/文件）验证不重渲染、样式内联、图片上传替换；
4. 用 mock 客户端跑草稿故障矩阵（拒绝/超时/提交后 commit 失败/重复调用）；
5. 输出 report.json 与 REGRESSION_REPORT.md，失败时退出码非零。

用法（仓库根目录）：
    uv run python scripts/run_sample_regression.py [--out outputs/regression]

安全边界：只做本地预览、导出与模拟草稿接口；保持 draft_only，不对外发布。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

REPRESENTATIVE_THEMES = ["wechat_baseline", "default", "morandi_forest"]
VIEWPORTS = [375, 430]


def setup_isolated_env(run_root: Path) -> dict[str, str]:
    env = {
        "WCM_DATABASE_URL": f"sqlite:///{run_root / 'regression.db'}",
        "WCM_OUTPUT_DIR": str(run_root / "outputs"),
        "WCM_UPLOAD_DIR": str(run_root / "uploads"),
        "WCM_ARTICLE_OUTBOX_DIR": str(run_root / "outbox" / "articles"),
        "WCM_VIDEO_OUTBOX_DIR": str(run_root / "outbox" / "videos"),
        "WCM_SOURCE_REPO_DIR": str(run_root / "source"),
        "WCM_WECHAT_DRAFT_DIR": str(run_root / "source" / "草稿"),
        "WCM_WECHAT_PENDING_DIR": str(run_root / "source" / "待发布"),
        "WCM_WECHAT_PUBLISHED_BACKUP_DIR": str(run_root / "source" / "备份"),
    }
    import os

    os.environ.update(env)
    return env


class LocalMockClient:
    """模拟微信客户端：只记录调用，绝不访问真实接口。"""

    calls: list[str] = []
    draft_payloads: list[dict] = []

    def __init__(self, session, account):
        pass

    def get_access_token(self, *, force_refresh: bool = False) -> str:
        return "mock-token"

    def upload_inline_image(self, path):
        type(self).calls.append("upload_inline")
        return {"url": f"https://mmbiz.qpic.cn/mock/inline/{Path(path).name}"}

    def upload_permanent_image(self, path):
        type(self).calls.append("upload_cover")
        return {"media_id": "MOCK_COVER", "url": f"https://mmbiz.qpic.cn/mock/cover/{Path(path).name}"}

    def add_draft(self, articles):
        type(self).calls.append("add_draft")
        type(self).draft_payloads.append(articles[0])
        return {"media_id": "MOCK_DRAFT_MEDIA"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="outputs/regression", help="报告与预览输出目录（默认 outputs/regression）")
    args = parser.parse_args()

    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""))
        return ok

    with tempfile.TemporaryDirectory(prefix="qwc-regression-") as tmp:
        setup_isolated_env(Path(tmp))

        # 延迟导入：确保在环境变量设置之后读取 Settings
        from bs4 import BeautifulSoup

        import qiao_wechat.services.publisher as publisher_module
        from qiao_wechat.db import Base
        from qiao_wechat.models import Article, WeChatAccount
        from qiao_wechat.services.html_normalizer import HtmlNormalizer
        from qiao_wechat.services.publisher import PublishService
        from qiao_wechat.services.renderer import WeChatMarkdownRenderer
        from qiao_wechat.config import get_settings

        settings = get_settings()
        out_root = (REPO_ROOT / args.out).resolve()
        previews_dir = out_root / "previews"
        previews_dir.mkdir(parents=True, exist_ok=True)

        sample_dir = REPO_ROOT / "examples" / "regression"
        sample_md_path = sample_dir / "sample_full.md"
        sample_md = sample_md_path.read_text(encoding="utf-8")
        sample_html = (sample_dir / "sample_full.html").read_text(encoding="utf-8")

        # ---------- 1. Markdown 排版：代表性主题渲染 ----------
        for theme in REPRESENTATIVE_THEMES:
            renderer = WeChatMarkdownRenderer(theme_name=theme)
            html = renderer.render(sample_md, title="排版回归样本：全要素覆盖")
            soup = BeautifulSoup(html, "html.parser")
            ok = all(
                [
                    soup.find("h1") is not None,
                    soup.find("table") is not None,
                    soup.find("pre") is not None,
                    soup.find("section", class_="callout") is not None,
                    soup.find(attrs={"data-citations": "1"}) is not None,
                    any("kroki.io" in (img.get("src") or "") for img in soup.find_all("img")),
                    any("latex.codecogs.com" in (img.get("src") or "") for img in soup.find_all("img")),
                ]
            )
            check(f"markdown 渲染结构完整 · {theme}", ok)
            pre = soup.find("pre")
            style = (pre.get("style") or "") + "".join(c.get("style") or "" for c in pre.find_all("code"))
            check(f"代码块换行安全(pre-wrap) · {theme}", "pre-wrap" in style)
            out_file = previews_dir / f"md_{theme}_full.html"
            renderer.save_preview(html, out_file, page_title=f"markdown · {theme}")

        # ---------- 2. HTML 入口：不重渲染 + 样式内联 + 图片替换 ----------
        publisher_module.WeChatApiClient = LocalMockClient
        LocalMockClient.calls = []
        LocalMockClient.draft_payloads = []

        engine_module = __import__("sqlalchemy")
        engine = engine_module.create_engine(settings.database_url, future=True)
        Base.metadata.create_all(engine)
        db = engine_module.orm.sessionmaker(bind=engine, future=True)()
        try:
            account = WeChatAccount(name="regression", appid="wx_mock", raw_secret="s", default_cover_media_id="DEFAULT_COVER")
            db.add(account)
            db.commit()

            def forbidden_render(self, markdown_text, *, title=None):
                raise AssertionError("HTML 入口不得调用 Markdown 渲染")

            original_render = WeChatMarkdownRenderer.render
            WeChatMarkdownRenderer.render = forbidden_render
            try:
                service = PublishService(db)
                html_article = service.create_html_file_draft(
                    account_id=account.id,
                    html_path=str(sample_dir / "sample_full.html"),
                    title="外部 HTML 排版样本",
                    cover_source=str(sample_dir / "assets" / "cover.png"),
                )
            finally:
                WeChatMarkdownRenderer.render = original_render

            html = html_article.html
            check("HTML 入口未调用 Markdown 渲染", True)
            check("HTML head 样式已内联", "border-left:4px solid #07c160" in html)
            check("HTML 相对图片已上传替换", "mmbiz.qpic.cn/mock/inline/transparent.png" in html)
            check("HTML 内容次序保留", html.find("<h1") < html.find("<blockquote") < html.find("<table") < html.find("<pre"))
            check(
                "HTML 本地预览已生成",
                bool(html_article.local_preview_path) and Path(html_article.local_preview_path).exists(),
            )
            WeChatMarkdownRenderer(theme_name=REPRESENTATIVE_THEMES[0]).save_preview(
                HtmlNormalizer().compact(html),
                previews_dir / "html_ingress_full.html",
                page_title="html ingress",
            )

            # 幂等复用
            service2 = PublishService(db)
            reused = service2.create_html_file_draft(
                account_id=account.id,
                html_path=str(sample_dir / "sample_full.html"),
                title="外部 HTML 排版样本",
                cover_source=str(sample_dir / "assets" / "cover.png"),
            )
            add_calls = LocalMockClient.calls.count("add_draft")
            check("重复提交复用远端结果（add_draft 仅 1 次）", add_calls == 1 and reused.meta.get("draft_outcome") == "reused")

            # ---------- 3. Markdown 草稿闭环 + 预览物化 ----------
            md_article = PublishService(db).create_article(
                account_id=account.id,
                title="排版回归样本：全要素覆盖",
                markdown=sample_md,
            )
            md_article.meta = {**(md_article.meta or {}), "source_path": str(sample_md_path)}
            db.commit()
            rendered = PublishService(db).render_article(md_article.id, upload_inline_images=False)
            preview = Path(rendered.local_preview_path)
            soup = BeautifulSoup(preview.read_text(encoding="utf-8"), "html.parser")
            materialized = [img["src"] for img in soup.find_all("img") if (img.get("src") or "").startswith("assets/")]
            check(
                "预览相对图片已物化",
                bool(materialized) and all((preview.parent / s).is_file() for s in materialized),
                f"materialized={materialized}",
            )
            for vp in VIEWPORTS:
                vp_file = previews_dir / f"viewport_{vp}px_check.html"
                vp_file.write_text(
                    preview.read_text(encoding="utf-8").replace(
                        "</head>",
                        f"<style>body{{width:{vp}px;margin:0 auto;outline:1px solid #f00;}}</style></head>",
                    ),
                    encoding="utf-8",
                )
            check(f"375/430px 人工抽查文件已生成", (previews_dir / "viewport_375px_check.html").exists())

            # ---------- 4. 草稿故障矩阵（mock 注入） ----------
            import requests as requests_lib

            from qiao_wechat.models import JobStatus, PublishJob
            from qiao_wechat.services.wechat_client import WeChatError

            fault_account = WeChatAccount(name="fault", appid="wx_fault", raw_secret="s", default_cover_media_id="C")
            db.add(fault_account)
            db.commit()
            fault_article = Article(
                account_id=fault_account.id,
                title="故障注入样本",
                markdown="故障注入正文。",
            )
            db.add(fault_article)
            db.commit()

            matrix = [
                ("reject", WeChatError, "remote_rejected"),
                ("timeout", requests_lib.Timeout, "remote_result_unknown"),
            ]
            for behavior, expected_exc, expected_category in matrix:
                LocalMockClient.calls = []
                LocalMockClient.draft_payloads = []

                class BehaviorClient(LocalMockClient):
                    pass

                def make_add(behavior):
                    def add_draft(self, articles):
                        type(self).calls.append("add_draft")
                        if behavior == "reject":
                            raise WeChatError("WeChat API error 40007", {"errcode": 40007})
                        if behavior == "timeout":
                            raise requests_lib.Timeout("timeout after remote accepted")
                        return {"media_id": "MOCK_DRAFT_MEDIA"}

                    return add_draft

                BehaviorClient.add_draft = make_add(behavior)
                publisher_module.WeChatApiClient = BehaviorClient
                svc = PublishService(db)
                try:
                    svc.create_wechat_draft(fault_article.id)
                    check(f"故障注入 {behavior} 应抛异常", False, "没有抛异常")
                    continue
                except expected_exc:
                    pass
                except Exception as exc:  # noqa: BLE001
                    check(f"故障注入 {behavior} 异常类型", False, f"{type(exc).__name__}: {exc}")
                    continue
                db.rollback()  # 模拟外层回滚
                db.expire_all()
                art = db.get(Article, fault_article.id)
                job = (
                    db.query(PublishJob)
                    .filter(PublishJob.article_id == fault_article.id, PublishJob.channel == "wx_draft")
                    .order_by(PublishJob.id.desc())
                    .first()
                )
                outcome = "unknown" if expected_category == "remote_result_unknown" else "failed"
                check(
                    f"故障 {behavior}: 记录在外层回滚后留存",
                    art is not None and art.status.value == "failed" and job is not None and job.error_category == expected_category
                    and (art.meta or {}).get("draft_outcome") == outcome,
                )

            # 未知结果拦截盲重发
            publisher_module.WeChatApiClient = LocalMockClient
            LocalMockClient.calls = []
            try:
                PublishService(db).create_wechat_draft(fault_article.id)
                check("未知结果拦截盲重发", False, "未被拦截")
            except ValueError:
                check("未知结果拦截盲重发", LocalMockClient.calls.count("add_draft") == 0)

            # force_new 放行
            forced = PublishService(db).create_wechat_draft(fault_article.id, force_new=True)
            check("force_new 显式放行", forced.wx_draft_media_id == "MOCK_DRAFT_MEDIA")

            # 重复调用复用
            LocalMockClient.calls = []
            PublishService(db).create_wechat_draft(fault_article.id)
            check("重复调用复用（不再调用远端）", LocalMockClient.calls.count("add_draft") == 0)
        finally:
            db.close()

    # ---------- 报告 ----------
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scope": "local preview / export / mock draft API only; draft_only preserved",
        "themes": REPRESENTATIVE_THEMES,
        "viewports_for_manual_check": VIEWPORTS,
        "checks": checks,
        "passed": sum(1 for c in checks if c["ok"]),
        "failed": sum(1 for c in checks if not c["ok"]),
    }
    out_root_local = REPO_ROOT / args.out
    out_root_local.mkdir(parents=True, exist_ok=True)
    (out_root_local / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 样本回归报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 结果：{report['passed']} 通过 / {report['failed']} 失败",
        f"- 边界：仅本地预览与模拟草稿接口（draft_only），未访问微信接口",
        "",
        "| 检查项 | 结果 |",
        "| --- | --- |",
    ]
    for c in checks:
        lines.append(f"| {c['name']} | {'✅' if c['ok'] else '❌ ' + c.get('detail', '')} |")
    lines += ["", f"预览文件：`{args.out}/previews/`（浏览器在 375/430px 宽度下人工抽查）", ""]
    (out_root_local / "REGRESSION_REPORT.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"\n报告: {out_root_local / 'REGRESSION_REPORT.md'}")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
