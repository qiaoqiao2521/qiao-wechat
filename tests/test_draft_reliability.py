"""草稿接口可靠性回归：故障注入、状态留存、未知结果与重复调用。

覆盖用户验收矩阵：
- 远端明确拒绝 → 外层回滚后失败记录可查询（新会话）
- 远端成功但本地超时 → 结果未知可区分，盲重发被拦截，force_new 显式放行
- 远端成功但本地提交失败 → 独立会话兜底留存 unknown 记录
- 重复调用 → 复用已录得的 media_id，远端只调用一次
- 内容修订 → 产生新草稿；旧库迁移补列不丢数据
"""

from __future__ import annotations

import sqlite3

import pytest
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import qiao_wechat.services.publisher as publisher_module
from qiao_wechat.config import Settings
from qiao_wechat.db import Base, _ensure_schema_columns
from qiao_wechat.models import Article, JobStatus, PublishChannel, PublishJob, WeChatAccount
from qiao_wechat.services.publisher import PublishService
from qiao_wechat.services.wechat_client import WeChatError


class FaultInjectableClient:
    """Mock WeChat client，可注入拒绝/超时/成功，并记录远端调用。"""

    behavior = "ok"
    calls: list[str] = []
    uploaded: list[str] = []

    def __init__(self, session, account):
        pass

    def get_access_token(self, *, force_refresh: bool = False) -> str:
        return "fake-token"

    def upload_permanent_image(self, path):
        type(self).uploaded.append(str(path))
        return {"media_id": "COVER_MEDIA_1", "url": "https://mmbiz.qpic.cn/cover"}

    def upload_inline_image(self, path):
        type(self).uploaded.append(str(path))
        return {"url": "https://mmbiz.qpic.cn/inline"}

    def add_draft(self, articles):
        type(self).calls.append("add_draft")
        behavior = type(self).behavior
        if behavior == "reject":
            raise WeChatError("WeChat API error 40007", {"errcode": 40007, "errmsg": "invalid media_id"})
        if behavior == "timeout":
            # 远端已受理但响应丢失，本地只能看到超时
            raise requests.Timeout("read timeout after remote accepted")
        return {"media_id": "DRAFT_MEDIA_OK"}

    def get_draft(self, media_id):
        return {"news_item": []}


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    yield session
    session.close()


@pytest.fixture()
def fresh_session_factory(db):
    def _make():
        return sessionmaker(bind=db.get_bind(), future=True)()

    return _make


@pytest.fixture()
def account(db):
    acct = WeChatAccount(name="main", appid="wx_test", raw_secret="secret", default_cover_media_id="ACCOUNT_COVER")
    db.add(acct)
    db.commit()
    return acct


@pytest.fixture()
def article(db, account):
    art = Article(
        account_id=account.id,
        title="故障注入样本",
        markdown="这是正文内容。",
        cover_source=None,
    )
    db.add(art)
    # 与生产一致：文章创建是独立事务，草稿尝试发生在这之后。
    db.commit()
    return art


@pytest.fixture()
def inject(monkeypatch):
    FaultInjectableClient.behavior = "ok"
    FaultInjectableClient.calls = []
    FaultInjectableClient.uploaded = []
    monkeypatch.setattr(publisher_module, "WeChatApiClient", FaultInjectableClient)
    return FaultInjectableClient


def _latest_wx_draft_job(session, article_id):
    return (
        session.query(PublishJob)
        .filter(PublishJob.article_id == article_id, PublishJob.channel == PublishChannel.wx_draft)
        .order_by(PublishJob.id.desc())
        .first()
    )


def test_rejected_draft_failure_record_survives_rollback(db, article, inject, fresh_session_factory):
    """R04：远端明确拒绝后，外层 rollback 不得抹掉失败记录。"""
    inject.behavior = "reject"
    service = PublishService(db)
    with pytest.raises(WeChatError):
        service.create_wechat_draft(article.id)
    db.rollback()  # 模拟 API get_db / db_session 的外层回滚

    check = fresh_session_factory()
    try:
        art = check.get(Article, article.id)
        assert art is not None, "article 行必须在外层回滚后仍然存在"
        assert art.status.value == "failed"
        job = _latest_wx_draft_job(check, article.id)
        assert job is not None
        assert job.status == JobStatus.failed
        assert job.error_category == "remote_rejected"
        assert "40007" in (job.error_message or "")
    finally:
        check.close()


def test_timeout_draft_marks_unknown_and_blocks_retry(db, article, inject, fresh_session_factory):
    """R05/R06：超时 → unknown 状态持久化；盲重发被拦截；force_new 显式放行。"""
    inject.behavior = "timeout"
    service = PublishService(db)
    with pytest.raises(requests.Timeout):
        service.create_wechat_draft(article.id)
    db.rollback()

    check = fresh_session_factory()
    try:
        art = check.get(Article, article.id)
        job = _latest_wx_draft_job(check, article.id)
        assert job.error_category == "remote_result_unknown"
        assert (art.meta or {}).get("draft_outcome") == "unknown"
        assert (art.meta or {}).get("draft_reconcile_hint")
    finally:
        check.close()

    # 盲重发被拦截，远端不产生第二个草稿
    retry = PublishService(db)
    with pytest.raises(ValueError, match="unknown remote outcome"):
        retry.create_wechat_draft(article.id)
    assert len(inject.calls) == 1

    # 显式 force_new（先人工核对草稿箱后）放行
    inject.behavior = "ok"
    forced = PublishService(db)
    forced_article = forced.create_wechat_draft(article.id, force_new=True)
    assert forced_article.wx_draft_media_id == "DRAFT_MEDIA_OK"
    assert len(inject.calls) == 2


def test_success_commit_failure_records_unknown_result(db, article, inject, fresh_session_factory, monkeypatch):
    """远端受理成功但本地 commit 失败：远端只调用一次，且兜底留存 unknown 记录。"""
    original_commit = db.commit
    fail_once = {"flag": True}

    def failing_commit():
        if fail_once["flag"]:
            fail_once["flag"] = False
            raise RuntimeError("simulated database failure at commit")
        return original_commit()

    # 只让草稿成功后的那次 commit 失败：跳过 prepare 阶段的内部提交路径
    inject.behavior = "ok"
    monkeypatch.setattr(db, "commit", failing_commit)
    service = PublishService(db)
    with pytest.raises(RuntimeError, match="simulated database failure"):
        service.create_wechat_draft(article.id)
    assert len(inject.calls) == 1, "远端草稿只应被创建一次"
    monkeypatch.undo()

    check = fresh_session_factory()
    try:
        recovered = check.query(Article).filter(Article.title == "故障注入样本").all()
        assert recovered, "必须有兜底留存的记录"
        target = next((a for a in recovered if (a.meta or {}).get("draft_outcome") == "unknown"), None)
        assert target is not None, "兜底记录必须标记 draft_outcome=unknown"
        job = _latest_wx_draft_job(check, target.id)
        assert job is not None
        assert job.error_category == "remote_result_unknown"
        assert "local persistence failed" in (job.error_message or "")
    finally:
        check.close()

    # 结果未知 → 再次提交默认被拦截
    with pytest.raises(ValueError, match="unknown remote outcome"):
        PublishService(db).create_wechat_draft(article.id)


def test_duplicate_create_reuses_media_id(db, article, inject):
    """R05：同账号同内容重复调用复用已录得结果，远端只 add 一次。"""
    inject.behavior = "ok"
    first = PublishService(db).create_wechat_draft(article.id)
    db.expire_all()
    second = PublishService(db).create_wechat_draft(article.id)
    assert len(inject.calls) == 1, "重复调用不得重复创建远端草稿"
    assert second.wx_draft_media_id == first.wx_draft_media_id == "DRAFT_MEDIA_OK"
    assert (second.meta or {}).get("draft_outcome") == "reused"


def test_content_revision_change_creates_new_draft(db, article, inject):
    """同账号内容修订（重新渲染后 revision 变化）→ 允许创建新草稿。"""
    inject.behavior = "ok"
    service = PublishService(db)
    service.create_wechat_draft(article.id)
    article.markdown = "这是修订后的正文内容，包含新的段落。"
    service.render_article(article.id)
    service.create_wechat_draft(article.id)
    assert len(inject.calls) == 2, "内容修订应产生新草稿而不是复用"


def test_legacy_sqlite_migration_adds_columns_without_data_loss(tmp_path):
    """旧版 SQLite 库（无新列）经 init_db 迁移补列且数据保留。"""
    legacy_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy_path)
    conn.execute(
        """
        CREATE TABLE publish_jobs (
            id INTEGER PRIMARY KEY,
            article_id INTEGER NOT NULL,
            account_id INTEGER,
            channel VARCHAR(32) NOT NULL,
            status VARCHAR(32) NOT NULL,
            request_json JSON,
            response_json JSON,
            error_message TEXT,
            started_at DATETIME,
            finished_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "INSERT INTO publish_jobs (article_id, channel, status, error_message) VALUES (1, 'wx_draft', 'failed', 'legacy failure')"
    )
    conn.commit()
    conn.close()

    from sqlalchemy import create_engine as ce

    legacy_engine = ce(f"sqlite:///{legacy_path}", future=True)
    _ensure_schema_columns(legacy_engine)

    check = sqlite3.connect(legacy_path)
    try:
        columns = {row[1] for row in check.execute("PRAGMA table_info(publish_jobs)").fetchall()}
        assert {"error_category", "request_key"} <= columns
        rows = check.execute("SELECT article_id, status, error_message FROM publish_jobs").fetchall()
        assert rows == [(1, "failed", "legacy failure")]
    finally:
        check.close()


def test_draft_only_defaults_preserved():
    """兼容守卫：默认策略必须保持 draft_only，预览与发布开关关闭。"""
    settings = Settings()
    assert settings.publish_policy == "draft_only"
    assert settings.allow_wechat_preview is False
    assert settings.allow_wechat_publish is False
