"""四项能力补齐的回归：深色模式预览、批量创建草稿、XHS 图片卡片规划、主题扩充。

背景（findings F24 的缺口清单，除插件系统外全部补齐）：
- 深色模式预览：预览页内置 自动/浅色/深色(模拟) 切换，近似微信深色阅读效果。
- 批量创建草稿：draft-only；单篇失败不中断；逐篇真实反馈（新建/复用/失败）。
- XHS 图片卡片规划：导出 cards.json + cards.md 人工制卡规划，不触碰平台接口。
- 主题扩充：scripts/import_external_themes.py 从 xiaohu-wechat-format 导入（xh_ 前缀）。
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import qiao_wechat.app as app_module
import qiao_wechat.cli as cli_module
import qiao_wechat.services.publisher as publisher_module
from qiao_wechat.cli import build_parser
from qiao_wechat.db import Base
from qiao_wechat.models import Article, WeChatAccount
from qiao_wechat.services.renderer import WeChatMarkdownRenderer
from qiao_wechat.services.xhs_exporter import XhsExporter


class OkClient:
    calls: list[str] = []

    def __init__(self, session, account):
        pass

    def get_access_token(self, *, force_refresh: bool = False):
        return "fake-token"

    def add_draft(self, articles):
        type(self).calls.append("add_draft")
        return {"media_id": f"BATCH_DRAFT_{len(type(self).calls)}"}


def _db_with_articles(count: int = 3):
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, future=True)()
    account = WeChatAccount(name="batch", appid="wx_batch", raw_secret="s", default_cover_media_id="C")
    db.add(account)
    db.commit()
    ids = []
    for i in range(count):
        art = Article(account_id=account.id, title=f"批量样本 {i}", markdown=f"批量正文 {i}。")
        db.add(art)
        db.flush()
        ids.append(art.id)
    db.commit()
    return db, ids


# ---------- 1. 深色模式预览 ----------


def test_preview_has_dark_mode_toggle():
    renderer = WeChatMarkdownRenderer()
    html = renderer.render("# 深色预览\n\n正文。", title="深色预览")
    out = Path("/tmp") / "qwc-dark-test" / "preview.html"
    renderer.save_preview(html, out)
    page = out.read_text(encoding="utf-8")
    assert "dark-toggle" in page and "dark-sim" in page, "预览页应有深色模拟切换"
    assert "prefers-color-scheme" in page, "自动模式应跟随系统深色偏好"
    assert "invert" in page, "深色模拟应使用反相近似"
    assert "img" in page.split("dark-sim ")[1].split("{{")[0] or ".dark-sim img" in page, "图片应反相还原"


# ---------- 2. 批量创建草稿 ----------


def test_batch_draft_mixed_outcomes_and_failure_isolation(monkeypatch):
    """批量：成功/复用/失败互不影响；结果逐篇真实。"""
    db, ids = _db_with_articles(3)
    # 让第二篇封面缺失 → 单篇失败
    db.get(Article, ids[1]).cover_source = "definitely/missing.png"
    db.get(Article, ids[1]).meta = {"no_default_cover": True}
    # 账号去掉默认封面，只让第二篇失败：改为给一三正常、第二篇删默认 cover
    for art_id in (ids[0], ids[2]):
        pass
    account = db.query(WeChatAccount).first()
    # 用两个账号隔离：第二篇挂到无封面的账号
    poor = WeChatAccount(name="poor", appid="wx_poor", raw_secret="s", default_cover_media_id=None)
    db.add(poor)
    db.commit()
    db.get(Article, ids[1]).account_id = poor.id
    db.commit()

    monkeypatch.setattr(publisher_module, "WeChatApiClient", OkClient)
    OkClient.calls = []

    service = publisher_module.PublishService(db)
    summary = service.create_wechat_drafts_batch(ids)

    assert summary["total"] == 3
    assert summary["created"] == 2
    assert summary["failed"] == 1
    by_id = {r["article_id"]: r for r in summary["results"]}
    assert by_id[ids[0]]["ok"] and by_id[ids[0]]["media_id"]
    assert not by_id[ids[1]]["ok"] and "image not found" in by_id[ids[1]]["error"]
    assert by_id[ids[2]]["ok"]


def test_batch_draft_reuses_duplicates(monkeypatch):
    """批量内与跨批量的重复内容都走复用，远端只调一次。"""
    db, ids = _db_with_articles(2)
    monkeypatch.setattr(publisher_module, "WeChatApiClient", OkClient)
    OkClient.calls = []

    service = publisher_module.PublishService(db)
    first = service.create_wechat_drafts_batch(ids)
    assert first["created"] == 2 and first["reused"] == 0
    second = service.create_wechat_drafts_batch(ids)
    assert second["reused"] == 2 and second["created"] == 0
    assert OkClient.calls.count("add_draft") == 2, "两篇各建一次，重复批不得再调远端"


def test_cli_draft_batch_outputs_summary(monkeypatch, capsys):
    db, ids = _db_with_articles(3)
    monkeypatch.setattr(cli_module, "init_db", lambda: None)

    @contextmanager
    def fake_db_session():
        yield db

    monkeypatch.setattr(cli_module, "db_session", fake_db_session)
    monkeypatch.setattr(publisher_module, "WeChatApiClient", OkClient)
    OkClient.calls = []

    parser = build_parser()
    args = parser.parse_args(["draft-batch", "--article-id", f"{ids[0]},{ids[1]}", "--article-id", str(ids[2])])
    cli_module.cmd_draft_batch(args)
    out = capsys.readouterr().out
    assert "新建 3" in out and "复用 0" in out

    # 再跑一遍 → 全部复用
    cli_module.cmd_draft_batch(args)
    out2 = capsys.readouterr().out
    assert "复用 3" in out2
    assert OkClient.calls.count("add_draft") == 3


def test_batch_api_endpoint(monkeypatch):
    db, ids = _db_with_articles(2)
    monkeypatch.setattr(publisher_module, "WeChatApiClient", OkClient)
    OkClient.calls = []

    def override_db():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app_module.app.dependency_overrides[app_module.get_db] = override_db
    try:
        client = TestClient(app_module.app)
        response = client.post(
            "/wechat/drafts/batch",
            json={"article_ids": ids},
        )
        assert response.status_code == 200, response.text
        summary = response.json()
        assert summary["created"] == 2
        assert {r["article_id"] for r in summary["results"]} == set(ids)
    finally:
        app_module.app.dependency_overrides.pop(app_module.get_db, None)


# ---------- 3. XHS 图片卡片规划 ----------


def test_xhs_cards_planned_as_export_artifacts(tmp_path: Path):
    """卡片规划：封面卡 + 分节内容卡 + 标签；只产出文件，不触平台。"""
    from qiao_wechat.config import Settings

    settings = Settings()
    settings.output_dir = tmp_path
    db = _db_with_articles(1)[0]
    article = db.get(Article, db.query(Article).first().id)
    article.markdown = (
        "# 标题\n\n开头钩子句。这里是补充说明。\n\n"
        "## 第一个要点\n\n第一个要点的内容，解释清楚。\n\n"
        "## 第二个要点\n\n第二个要点的内容，给出案例。\n"
    )
    db.commit()

    exporter = XhsExporter(db)
    exporter.settings = settings
    note_path = exporter.export_article(article.id, tags=["排版", "微信"])
    cards_path = note_path.parent / "cards.json"
    assert cards_path.is_file(), "应产出 cards.json"
    assert (note_path.parent / "cards.md").is_file()

    import json

    cards = json.loads(cards_path.read_text(encoding="utf-8"))
    assert cards["cards"][0]["kind"] == "cover"
    assert "标题" in cards["cards"][0]["title"] or cards["cards"][0]["title"]
    content_cards = [c for c in cards["cards"] if c["kind"] == "content"]
    assert len(content_cards) >= 2, "两个二级标题应生成至少两张内容卡"
    assert cards["tags"] == ["#排版", "#微信"]
    assert all(c.get("hint") for c in cards["cards"]), "每张卡应有制卡提示"
    # 仍是安全导出：不触碰平台
    assert "人工" in cards["note"]


# ---------- 4. 主题扩充 ----------


def test_theme_count_expanded_to_at_least_30():
    themes = WeChatMarkdownRenderer.available_themes()
    assert len(themes) >= 30, f"主题应扩充到 30+，当前 {len(themes)}"
    xh_themes = [t for t in themes if t.startswith("xh_")]
    assert len(xh_themes) >= 30, "xiaohu 导入主题应至少 30 套"


@pytest.mark.parametrize("theme", ["xh_github", "xh_dark_ocean", "xh_wechat_native", "xh_terminal_matrix"])
def test_imported_themes_render_with_inline_styles(theme):
    assert theme in WeChatMarkdownRenderer.available_themes()
    html = WeChatMarkdownRenderer(theme_name=theme).render(
        "# 标题\n\n正文段落。\n\n> 引用\n\n- 列表项\n", title="主题验证"
    )
    assert html.count('style="') >= 5, f"{theme}: 内联样式过少"
    assert f"theme-{theme}" in html


def test_imported_theme_code_blocks_wrap_safely():
    """导入主题必须通过微信安全换行检查（与自带主题同一契约）。"""
    sample = "# t\n\n```python\nx = 1\n```\n"
    for theme in [t for t in WeChatMarkdownRenderer.available_themes() if t.startswith("xh_")][:10]:
        html = WeChatMarkdownRenderer(theme_name=theme).render(sample, title="t")
        soup = BeautifulSoup(html, "html.parser")
        pre = soup.find("pre")
        style = (pre.get("style") or "") + "".join(c.get("style") or "" for c in pre.find_all("code"))
        assert "pre-wrap" in style, f"{theme}: pre 未设置 pre-wrap"
