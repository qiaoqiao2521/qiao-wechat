"""CapMesh 加固回归：持久化 unknown 后的 CLI 默认重试必须给出可执行反馈。

新增覆盖边界（现有测试未覆盖的合取）：
- 第一次远端已受理但本地超时，服务内即时 commit，把 remote_result_unknown
  留痕（现有 test_operation_feedback 只覆盖同一次 cmd_draft 内的首次 Timeout）；
- 随后用**新的 DB session** 走真实 ``cli.cmd_draft``（force_new=False），命中
  ``publisher._raise_if_outcome_unknown`` 的持久化拦截（ValueError），
  而不是再次 ``requests.Timeout``；
- CLI 以退出码 1 结束，stderr 给出“先核对草稿箱”的可执行指引；
- 整个重试过程不得再次调用 ``client.add_draft``。

独立运行：临时 SQLite 文件、tmp_path 输出路径、fake WeChat client；
monkeypatch ``publisher_module.WeChatApiClient``，不触碰真实微信或网络。

CAPMESH_MUTANT=1：仅在该环境变量下 monkeypatch ``cli._friendly_draft_error``
的最小变体，使 unknown 结果丢失“核对草稿箱 / --force-new”指引。正常实现通过，
变体由针对真实 ``cmd_draft`` stderr 与退出码的业务断言检出，不检查环境变量本身。
"""

from __future__ import annotations

import argparse
import os
from contextlib import contextmanager

import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import qiao_wechat.cli as cli_module
import qiao_wechat.services.publisher as publisher_module
from qiao_wechat.config import get_settings
from qiao_wechat.db import Base
from qiao_wechat.models import Article, JobStatus, PublishChannel, PublishJob, WeChatAccount

# 真实实现引用：CAPMESH_MUTANT 变体只在 unknown 分支替换，其余分类仍委托真实实现。
_REAL_FRIENDLY_DRAFT_ERROR = cli_module._friendly_draft_error


class TimeoutThenForbidRetryClient:
    """第一次 add_draft 抛 Timeout；若被再次调用则失败（重试不应触达远端）。"""

    behavior = "timeout"
    calls: list[str] = []

    def __init__(self, session, account):
        pass

    def get_access_token(self, *, force_refresh: bool = False):
        return "fake-token"

    def upload_permanent_image(self, path):
        return {"media_id": "COVER", "url": "https://example.invalid/cover"}

    def upload_inline_image(self, path):
        return {"url": "https://example.invalid/inline"}

    def add_draft(self, articles):
        type(self).calls.append("add_draft")
        if type(self).behavior == "timeout":
            raise requests.Timeout("timeout after remote accepted")
        raise AssertionError("unknown 重试不应再次触达远端 draft/add")


def _mutant_friendly_draft_error(exc: Exception) -> str:
    """CAPMESH_MUTANT 变体：丢掉 unknown 结果的下一步指引。

    依据：真实 ``cli._friendly_draft_error``（cli.py:94）用
    ``"unknown remote outcome" in text``（cli.py:97）识别
    ``publisher._raise_if_outcome_unknown``（publisher.py:856）抛出的 ValueError，
    返回“核对草稿箱 / --force-new”指引。本变体把该分支降级为通用兜底文案，
    其余分类仍逐字委托真实实现。
    """
    if "unknown remote outcome" in str(exc):
        return (
            f"草稿创建失败：{exc}\n"
            "下一步：可用 `qiao-wechat jobs --article-id <id>` 查看已完成的步骤与错误类别。"
        )
    return _REAL_FRIENDLY_DRAFT_ERROR(exc)


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    """把运行产物隔离到 tmp_path，并在用例前后清理 get_settings 缓存。"""
    monkeypatch.setenv("WCM_OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.setenv("WCM_UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_persisted_unknown_retry_on_fresh_session_reports_draftbox(monkeypatch, capsys, isolated_env):
    tmp_path = isolated_env

    if os.environ.get("CAPMESH_MUTANT") == "1":
        monkeypatch.setattr(cli_module, "_friendly_draft_error", _mutant_friendly_draft_error)

    engine = create_engine(f"sqlite:///{tmp_path / 'capmesh.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as seed:
        account = WeChatAccount(
            name="capmesh",
            appid="wx_capmesh",
            raw_secret="s",
            default_cover_media_id="CAPMESH_COVER",
        )
        seed.add(account)
        seed.commit()
        article = Article(account_id=account.id, title="capmesh 样本", markdown="正文")
        seed.add(article)
        seed.commit()
        article_id = article.id

    @contextmanager
    def fresh_session():
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(cli_module, "init_db", lambda: None)
    monkeypatch.setattr(cli_module, "db_session", fresh_session)
    monkeypatch.setattr(publisher_module, "WeChatApiClient", TimeoutThenForbidRetryClient)
    TimeoutThenForbidRetryClient.behavior = "timeout"
    TimeoutThenForbidRetryClient.calls = []

    # 第一次：远端受理但本地超时 → 服务内即时留痕 unknown，CLI 退出 1。
    with pytest.raises(SystemExit) as first_exit:
        cli_module.cmd_draft(
            argparse.Namespace(article_id=article_id, no_upload_inline_images=False, force_new=False)
        )
    assert first_exit.value.code == 1, "首次超时必须非零退出"
    first_err = capsys.readouterr().err
    assert "草稿箱" in first_err, "首次超时应提示核对草稿箱"
    assert TimeoutThenForbidRetryClient.calls.count("add_draft") == 1

    # 新 DB session 内 unknown 留痕必须可查询，且 request_key 已持久化。
    with SessionLocal() as check:
        stored = check.get(Article, article_id)
        assert (stored.meta or {}).get("draft_outcome") == "unknown"
        job = (
            check.query(PublishJob)
            .filter(PublishJob.article_id == article_id, PublishJob.channel == PublishChannel.wx_draft)
            .order_by(PublishJob.id.desc())
            .first()
        )
        assert job is not None and job.status == JobStatus.failed
        assert job.error_category == "remote_result_unknown"
        assert job.request_key, "unknown 记录必须带 request_key 才能拦截盲重发"

    # 第二次：默认重试走新 session，命中持久化 unknown 拦截（非再次 Timeout）。
    with pytest.raises(SystemExit) as retry_exit:
        cli_module.cmd_draft(
            argparse.Namespace(article_id=article_id, no_upload_inline_images=False, force_new=False)
        )
    retry_err = capsys.readouterr().err

    assert retry_exit.value.code == 1, "unknown 拦截必须以非零退出码结束"
    assert "草稿箱" in retry_err and "核对" in retry_err, "默认重试必须提示先核对草稿箱"
    assert "--force-new" in retry_err, "必须给出人工核对后的放行方式"
    assert "Traceback" not in retry_err, "不得输出裸 traceback"
    assert TimeoutThenForbidRetryClient.calls.count("add_draft") == 1, "unknown 重试不得再次调用远端"
