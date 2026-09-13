"""阶段 4 操作反馈回归：工具页失败/成功/复用反馈、CLI 可执行提示、force_new 透传。

验收目标（task_plan 阶段 4）：一次失败后操作者可判断下一步，不需读 Python traceback。
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path

import requests
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import qiao_wechat.app as app_module
import qiao_wechat.cli as cli_module
import qiao_wechat.services.publisher as publisher_module
from qiao_wechat.cli import build_parser
from qiao_wechat.db import Base
from qiao_wechat.models import JobStatus, PublishChannel, PublishJob, WeChatAccount


class TimeoutThenOkClient:
    behavior = "timeout"
    calls: list[str] = []

    def __init__(self, session, account):
        pass

    def get_access_token(self, *, force_refresh: bool = False):
        return "fake-token"

    def upload_permanent_image(self, path):
        return {"media_id": "COVER", "url": "https://mmbiz.qpic.cn/cover"}

    def add_draft(self, articles):
        type(self).calls.append("add_draft")
        if type(self).behavior == "timeout":
            raise requests.Timeout("timeout after remote accepted")
        if type(self).behavior == "reject":
            from qiao_wechat.services.wechat_client import WeChatError

            raise WeChatError("WeChat API error 40007", {"errcode": 40007})
        return {"media_id": "DRAFT_OK"}


def _make_db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def test_tool_page_offers_failure_reuse_and_jobs_feedback():
    """工具页必须具备错误分类、复用标识、force_new 与任务记录查询入口。"""
    from fastapi.testclient import TestClient as _TC  # noqa: F401  (保持依赖显式)

    from qiao_wechat.app import app

    page = _TC(app).get("/tools/html-draft").text
    assert "force_new" in page, "应有强制新建开关"
    assert "强制新建草稿" in page
    assert "草稿箱" in page and "remote_result_unknown" in page, "未知结果应引导核对草稿箱"
    assert "classifyFailure" in page and "是否可能已提交" in page, "错误需分类并说明是否可能已提交"
    assert "复用已有草稿" in page, "重复提交应显示复用标识"
    assert "loadJobs" in page and "/jobs" in page and "jobs-article-id" in page, "应能按 Article ID 查询任务记录"
    assert "手机预览" in page and "默认关闭" in page, "应说明本地预览与手机预览权限差异"


def test_cli_draft_unknown_outcome_prints_actionable_error(monkeypatch, capsys):
    """CLI 在结果未知时输出下一步动作，而不是 traceback，退出码 1。"""
    db = _make_db()
    account = WeChatAccount(name="m", appid="wx", raw_secret="s", default_cover_media_id="C")
    db.add(account)
    db.commit()
    from qiao_wechat.models import Article

    article = Article(account_id=account.id, title="t", markdown="正文")
    db.add(article)
    db.commit()

    monkeypatch.setattr(cli_module, "init_db", lambda: None)

    @contextmanager
    def fake_db_session():
        yield db

    monkeypatch.setattr(cli_module, "db_session", fake_db_session)
    monkeypatch.setattr(publisher_module, "WeChatApiClient", TimeoutThenOkClient)
    TimeoutThenOkClient.behavior = "timeout"
    TimeoutThenOkClient.calls = []

    args = argparse.Namespace(article_id=article.id, no_upload_inline_images=False, force_new=False)
    try:
        cli_module.cmd_draft(args)
        raised = None
    except SystemExit as exc:
        raised = exc
    assert raised is not None and raised.code == 1, "失败必须以非零退出码结束"
    err = capsys.readouterr().err
    assert "草稿箱" in err and "--force-new" in err, "必须给出核对草稿箱与 force-new 两个下一步"
    assert "Traceback" not in err, "不允许裸 traceback"

    # 核对后 force_new 放行
    TimeoutThenOkClient.behavior = "ok"
    cli_module.cmd_draft(argparse.Namespace(article_id=article.id, no_upload_inline_images=False, force_new=True))
    out = capsys.readouterr().out
    assert "media_id=DRAFT_OK" in out


def test_cli_draft_reuse_hint_and_jobs_category(monkeypatch, capsys):
    """成功复用时提示未重复提交；jobs 输出 error_category。"""
    db = _make_db()
    account = WeChatAccount(name="m", appid="wx", raw_secret="s", default_cover_media_id="C")
    db.add(account)
    db.commit()
    from qiao_wechat.models import Article

    article = Article(account_id=account.id, title="t", markdown="正文")
    db.add(article)
    db.commit()

    monkeypatch.setattr(cli_module, "init_db", lambda: None)

    @contextmanager
    def fake_db_session():
        yield db

    monkeypatch.setattr(cli_module, "db_session", fake_db_session)
    monkeypatch.setattr(publisher_module, "WeChatApiClient", TimeoutThenOkClient)
    TimeoutThenOkClient.behavior = "ok"
    TimeoutThenOkClient.calls = []

    args = argparse.Namespace(article_id=article.id, no_upload_inline_images=False, force_new=False)
    cli_module.cmd_draft(args)
    capsys.readouterr()
    cli_module.cmd_draft(args)
    out = capsys.readouterr().out
    assert "复用已录得结果" in out and "未重复提交" in out
    assert TimeoutThenOkClient.calls.count("add_draft") == 1

    # 构造一条带类别的失败 job，验证 jobs 输出
    db.add(
        PublishJob(
            article_id=article.id,
            account_id=account.id,
            channel=PublishChannel.wx_draft,
            status=JobStatus.failed,
            error_category="remote_result_unknown",
            error_message="timeout",
        )
    )
    db.commit()
    cli_module.cmd_jobs(argparse.Namespace(article_id=article.id, limit=10))
    jobs_out = capsys.readouterr().out
    assert "category=remote_result_unknown" in jobs_out


def test_failure_detail_carries_article_hint(monkeypatch):
    """失败详情附带 article_id 提示，操作者能直接查任务记录。"""
    from qiao_wechat.services.wechat_client import WeChatError as _WE

    # TestClient 的请求在事件循环线程执行：内存库需要 StaticPool + 关闭线程检查
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, future=True)()
    account = WeChatAccount(name="m", appid="wx", raw_secret="s", default_cover_media_id="COVER")
    db.add(account)
    db.commit()

    class RejectClient:
        def __init__(self, session, account_ref):
            pass

        def get_access_token(self, *, force_refresh: bool = False):
            return "fake-token"

        def add_draft(self, articles):
            raise _WE("WeChat API error 40007", {"errcode": 40007})

    monkeypatch.setattr(publisher_module, "WeChatApiClient", RejectClient)

    from qiao_wechat.app import app as _app
    from qiao_wechat.schemas import HtmlDraftCreate

    def override_db():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    _app.dependency_overrides[app_module.get_db] = override_db
    try:
        client = TestClient(_app, raise_server_exceptions=False)
        response = client.post(
            "/wechat/drafts/from-html",
            json={"account_id": account.id, "title": "t", "html": "<p>正文</p>"},
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "article_id=" in detail, f"失败详情应带 article_id 提示: {detail}"
        assert "40007" in detail
    finally:
        _app.dependency_overrides.pop(app_module.get_db, None)


def test_force_new_flag_reaches_parser_and_service():
    """html-draft / draft 的 --force-new 都能被解析。"""
    parser = build_parser()
    args = parser.parse_args(["draft", "1", "--force-new"])
    assert args.force_new is True
    args2 = parser.parse_args(["draft", "1"])
    assert args2.force_new is False


def test_from_html_force_new_and_reuse_flow(monkeypatch, tmp_path: Path):
    """API：同一 HTML 重复提交复用（add_draft 一次），force_new 放行第二次提交。"""
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)

    class CountingClient:
        calls: list[str] = []

        def __init__(self, session, account):
            pass

        def get_access_token(self, *, force_refresh: bool = False):
            return "fake-token"

        def add_draft(self, articles):
            type(self).calls.append("add_draft")
            return {"media_id": f"draft_{len(type(self).calls)}"}

    monkeypatch.setattr(publisher_module, "WeChatApiClient", CountingClient)

    def override_db():
        db = Session()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    with Session() as db:
        account = WeChatAccount(name="main", appid="wx_test", raw_secret="s", default_cover_media_id="cover")
        db.add(account)
        db.commit()
        account_id = account.id

    app_module.app.dependency_overrides[app_module.get_db] = override_db
    try:
        client = TestClient(app_module.app)
        payload = {
            "account_id": account_id,
            "title": "反馈链路样本",
            "html": "<section><h1>反馈链路样本</h1><p>正文</p></section>",
        }

        first = client.post("/wechat/drafts/from-html", json=payload)
        assert first.status_code == 200, first.text
        assert first.json()["wx_draft_media_id"] == "draft_1"
        assert (first.json()["meta"] or {}).get("draft_outcome") == "created"

        second = client.post("/wechat/drafts/from-html", json=payload)
        assert second.status_code == 200, second.text
        assert second.json()["wx_draft_media_id"] == "draft_1", "重复内容必须复用同一草稿"
        assert (second.json()["meta"] or {}).get("draft_outcome") == "reused"
        assert CountingClient.calls.count("add_draft") == 1, "复用不得再次调用远端"

        third = client.post("/wechat/drafts/from-html", json={**payload, "options": {"force_new": True}})
        assert third.status_code == 200, third.text
        assert third.json()["wx_draft_media_id"] == "draft_2"
        assert CountingClient.calls.count("add_draft") == 2, "force_new 应允许显式新建"
    finally:
        app_module.app.dependency_overrides.pop(app_module.get_db, None)
