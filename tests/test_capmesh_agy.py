"""CapMesh 回归与故障变体测试：文件 SQLite 跨连接未知结果持久化与防重发。

契约：
- 未知结果（requests.Timeout）跨数据库会话/进程持久化。
- 新增边界：基于本地临时文件 SQLite，关闭原 session 及 engine.dispose() 后重建连接。
- 重新构造服务并加载同一文章后，默认重试禁止重发（拦截并抛出用户可见错误，add_draft 调用次数保持为 1）。
- 显式 force_new=True 成功放行时，add_draft 调用总数才递增到 2。
- 变体注入（CAPMESH_MUTANT=1）：将 PublishService._raise_if_outcome_unknown 替换为 no-op，
  测试预期被业务断言（未拦截重试抛错 / add_draft 调用次数增加）检出。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import qiao_wechat.services.publisher as publisher_module
from qiao_wechat.config import Settings
from qiao_wechat.db import Base
from qiao_wechat.models import Article, ArticleStatus, JobStatus, PublishChannel, PublishJob, WeChatAccount
from qiao_wechat.services.publisher import PublishService
from qiao_wechat.services.wechat_client import WeChatError


class FakeWeChatApiClient:
    calls: list[str] = []
    uploaded: list[str] = []
    behavior: str = "ok"

    def __init__(self, session, account):
        self.session = session
        self.account = account

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
        if behavior == "timeout":
            raise requests.Timeout("mock remote timeout after draft request submitted")
        if behavior == "reject":
            raise WeChatError("WeChat API error 40007", {"errcode": 40007, "errmsg": "invalid media_id"})
        return {"media_id": "DRAFT_MEDIA_OK"}

    def get_draft(self, media_id):
        return {"news_item": []}


@pytest.fixture()
def fake_wechat(monkeypatch: pytest.MonkeyPatch):
    FakeWeChatApiClient.calls = []
    FakeWeChatApiClient.uploaded = []
    FakeWeChatApiClient.behavior = "ok"
    monkeypatch.setattr(publisher_module, "WeChatApiClient", FakeWeChatApiClient)

    # 变体注入：仅当 CAPMESH_MUTANT=1 时将 _raise_if_outcome_unknown 替换为 no-op
    if os.getenv("CAPMESH_MUTANT") == "1":
        monkeypatch.setattr(
            PublishService,
            "_raise_if_outcome_unknown",
            lambda self, account_id, request_key: None,
        )

    return FakeWeChatApiClient


def test_unknown_draft_outcome_persists_across_file_sqlite_sessions_and_blocks_retry(
    tmp_path: Path,
    fake_wechat,
    monkeypatch: pytest.MonkeyPatch,
):
    """跨文件 SQLite 连接验证未知结果持久化与重试阻断。"""
    db_file = tmp_path / "capmesh_persistence.db"
    db_url = f"sqlite:///{db_file}"

    outputs_dir = tmp_path / "outputs"
    uploads_dir = tmp_path / "uploads"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    test_settings = Settings(
        database_url=db_url,
        output_dir=outputs_dir,
        upload_dir=uploads_dir,
    )
    monkeypatch.setattr(publisher_module, "get_settings", lambda: test_settings)

    # 1. 建立初始连接并建表，初始化账号与文章
    engine1 = create_engine(db_url, future=True)
    Base.metadata.create_all(engine1)
    SessionFactory1 = sessionmaker(bind=engine1, future=True)
    session1 = SessionFactory1()

    account = WeChatAccount(
        name="capmesh_account",
        appid="wx_capmesh_app",
        raw_secret="capmesh_secret",
        default_cover_media_id="DEFAULT_COVER_MEDIA",
    )
    session1.add(account)
    session1.commit()

    article = Article(
        account_id=account.id,
        title="CapMesh 持久化与防重发边界测试",
        markdown="正文内容：测试文件 SQLite 跨 session 持久化。",
        cover_source=None,
    )
    session1.add(article)
    session1.commit()
    article_id = article.id

    # 2. 注入超时故障：发起草稿提交时抛出 requests.Timeout
    fake_wechat.behavior = "timeout"
    service1 = PublishService(session1)

    with pytest.raises(requests.Timeout):
        service1.create_wechat_draft(article_id)

    # 模拟外层回滚（如 FastAPI db_session 异常回滚），然后彻底关闭原 session 与底层 engine
    session1.rollback()
    session1.close()
    engine1.dispose()

    # 验证第 1 次提交确实触达了 add_draft 接口
    assert len(fake_wechat.calls) == 1
    assert fake_wechat.calls == ["add_draft"]

    # 3. 基于文件 SQLite 重新构造新 engine、新 session 并重新构造服务
    engine2 = create_engine(db_url, future=True)
    SessionFactory2 = sessionmaker(bind=engine2, future=True)
    session2 = SessionFactory2()

    # 断言 unknown 状态跨数据库连接持久化成功
    art = session2.get(Article, article_id)
    assert art is not None, "文章记录必须在关闭原 engine 后依然存在于文件 SQLite 中"
    assert art.status == ArticleStatus.failed
    assert (art.meta or {}).get("draft_outcome") == "unknown", "draft_outcome 必须持久化为 unknown"
    assert (art.meta or {}).get("draft_reconcile_hint") is not None, "必须持久化草稿箱人工核对提示"

    job = (
        session2.query(PublishJob)
        .filter(PublishJob.article_id == article_id, PublishJob.channel == PublishChannel.wx_draft)
        .order_by(PublishJob.id.desc())
        .first()
    )
    assert job is not None, "必须查询到持久化的草稿任务记录"
    assert job.status == JobStatus.failed
    assert job.error_category == "remote_result_unknown"

    # 4. 重新构造服务并尝试盲重发（模拟网络恢复）
    fake_wechat.behavior = "ok"
    service2 = PublishService(session2)

    with pytest.raises(ValueError, match="unknown remote outcome"):
        service2.create_wechat_draft(article_id)

    # 业务断言：未知结果重试必须被拦截，不得向远端发起第二次 add_draft 调用
    assert len(fake_wechat.calls) == 1, (
        f"在 remote_result_unknown 状态下盲重试应被拦截，禁止增加远端草稿调用；实际调用数: {len(fake_wechat.calls)}"
    )

    # 5. 显式指定 force_new=True 进行确认后放行
    forced_article = service2.create_wechat_draft(article_id, force_new=True)
    assert forced_article.status == ArticleStatus.draft_created
    assert forced_article.wx_draft_media_id == "DRAFT_MEDIA_OK"
    assert (forced_article.meta or {}).get("draft_outcome") == "created"

    # 业务断言：只有显式 force_new 放行成功时，add_draft 远端调用总数才从 1 增加到 2
    assert len(fake_wechat.calls) == 2, (
        f"显式 force_new=True 放行成功后调用总数应为 2；实际调用数: {len(fake_wechat.calls)}"
    )

    session2.close()
    engine2.dispose()
