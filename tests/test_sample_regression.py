"""排版样本回归：十类要素、代表性主题、HTML 不重渲染、预览与素材边界。

样本位于 examples/regression/。全部用本地渲染与 mock 客户端，不访问微信接口，
不需要真实凭据；图表/公式只断言 URL 结构，不依赖外网可达。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import qiao_wechat.services.publisher as publisher_module
from qiao_wechat.db import Base
from qiao_wechat.models import Article, WeChatAccount
from qiao_wechat.services.publisher import PublishService
from qiao_wechat.services.renderer import WeChatMarkdownRenderer

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = REPO_ROOT / "examples" / "regression"
SAMPLE_MD = (SAMPLE_DIR / "sample_full.md").read_text(encoding="utf-8")
SAMPLE_HTML = (SAMPLE_DIR / "sample_full.html").read_text(encoding="utf-8")

# 每个来源组取一个代表（见 themes/metadata.json 的 source 字段）：
# qiao-wechat 自研 / local wechat-format / WeMD
REPRESENTATIVE_THEMES = ["wechat_baseline", "default", "morandi_forest"]


class RecordingMockClient:
    """本地 mock：记录上传与草稿调用，不产生任何远端副作用。"""

    draft_payloads: list[dict] = []
    uploaded_inline: list[str] = []
    uploaded_cover: list[str] = []

    def __init__(self, session, account):
        pass

    def get_access_token(self, *, force_refresh: bool = False) -> str:
        return "mock-token"

    def upload_inline_image(self, path):
        type(self).uploaded_inline.append(str(path))
        return {"url": f"https://mmbiz.qpic.cn/mock/inline/{Path(path).name}"}

    def upload_permanent_image(self, path):
        type(self).uploaded_cover.append(str(path))
        return {"media_id": "MOCK_COVER", "url": f"https://mmbiz.qpic.cn/mock/cover/{Path(path).name}"}

    def add_draft(self, articles):
        type(self).draft_payloads.append(articles[0])
        return {"media_id": "MOCK_DRAFT_MEDIA"}


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    yield session
    session.close()


@pytest.fixture()
def mock(monkeypatch):
    RecordingMockClient.draft_payloads = []
    RecordingMockClient.uploaded_inline = []
    RecordingMockClient.uploaded_cover = []
    monkeypatch.setattr(publisher_module, "WeChatApiClient", RecordingMockClient)
    return RecordingMockClient


@pytest.fixture()
def account(db):
    acct = WeChatAccount(name="sample", appid="wx_sample", raw_secret="s", default_cover_media_id="DEFAULT_COVER")
    db.add(acct)
    db.commit()
    return acct


def _render(theme: str = "wechat_baseline") -> str:
    return WeChatMarkdownRenderer(theme_name=theme).render(SAMPLE_MD, title="排版回归样本：全要素覆盖")


def test_sample_covers_all_ten_element_classes():
    """十类要素：标题/列表/表格/代码/引用callout/图片/长链接/图表/公式/中文正文。"""
    html = _render()
    soup = BeautifulSoup(html, "html.parser")

    assert soup.find("h1"), "应有标题"
    assert len(soup.find_all(["h1", "h2"])) >= 4, "应有标题层级"
    assert soup.find("table") and len(soup.find_all("tr")) >= 3, "应有表格"
    assert soup.find("pre"), "应有代码块"
    assert soup.find("section", class_="callout"), "应有 callout"
    assert soup.find("blockquote"), "应有引用块"

    images = soup.find_all("img")
    srcs = [img.get("src", "") for img in images]
    assert any("kroki.io/plantuml" in s for s in srcs), "PlantUML 应转成 Kroki 图片"
    assert any("kroki.io/graphviz" in s for s in srcs), "Graphviz 应转成 Kroki 图片"
    assert any("latex.codecogs.com" in s for s in srcs), "公式应转成 CodeCogs 图片"
    assert soup.find("section", attrs={"data-citations": "1"}), "长链接应进入参考链接区"
    assert soup.find(string=lambda t: t and "全角标点" in t), "中文正文应保留"


def _prose_text(soup: BeautifulSoup) -> str:
    """正文文本（排除 code/pre——示例中故意引用的语法说明不算泄漏）。"""
    clone = BeautifulSoup(str(soup), "html.parser")
    for node in clone.find_all(["code", "pre"]):
        node.decompose()
    return clone.get_text()


def test_sample_no_source_leakage():
    """无源码泄漏：不残留 markdown 语法、公式定界符、图表源码。"""
    html = _render()
    soup = BeautifulSoup(html, "html.parser")
    text = _prose_text(soup)
    for leak in ["```", "$$", "\\int", "![", "](", "@startuml", "digraph G {"]:
        assert leak not in text, f"正文泄漏了源码标记: {leak}"
    # mermaid 不支持：源码保留在代码块中是预期边界，不应变成图片
    mermaid_pre = soup.find("pre", string=lambda t: t and "graph LR" in t)
    assert mermaid_pre, "mermaid 源码应作为代码块保留（当前不支持渲染的边界）"


def test_representative_themes_inline_styles_and_no_leak():
    """代表性主题：样式内联、要素完整、无源码泄漏。"""
    for theme in REPRESENTATIVE_THEMES:
        html = _render(theme)
        assert f"theme-{theme}" in html, f"{theme}: 根节点应带主题类"
        assert 'style="' in html, f"{theme}: 应有内联样式"
        assert "<style" not in html, f"{theme}: 不应残留 style 标签（微信不支持）"
        soup = BeautifulSoup(html, "html.parser")
        assert soup.find("table"), f"{theme}: 表格丢失"
        assert soup.find("pre"), f"{theme}: 代码块丢失"
        assert soup.find(attrs={"data-citations": "1"}), f"{theme}: 参考链接丢失"
        text = _prose_text(soup)
        assert "```" not in text, f"{theme}: 泄漏围栏标记"
        # 代码块必须换行安全（微信端无横向滚动）：pre 或其 code 上要有 pre-wrap
        pre = soup.find("pre")
        combined_style = (pre.get("style") or "") + "".join(code.get("style") or "" for code in pre.find_all("code"))
        assert "pre-wrap" in combined_style, f"{theme}: 代码块未设置 pre-wrap，长代码行在微信端会被裁剪"


def test_missing_image_error_locates_file(db, account, mock, tmp_path):
    """缺失图片：错误必须能定位到具体文件。"""
    missing_md = tmp_path / "missing.md"
    missing_md.write_text("# 缺图样本\n\n![不存在的图](assets/definitely_missing.png)\n", encoding="utf-8")
    article = PublishService(db).create_article(
        account_id=account.id,
        title="缺图样本",
        markdown=missing_md.read_text(encoding="utf-8"),
    )
    article.meta = {**(article.meta or {}), "source_path": str(missing_md)}
    db.commit()

    service = PublishService(db)
    with pytest.raises(FileNotFoundError, match="definitely_missing.png"):
        service.create_wechat_draft(article.id, upload_inline_images=True)


def test_transparent_png_upload_preserves_alpha(db, account, mock, tmp_path):
    """透明图：本地文件走上传 mock，保持 PNG 与 alpha 通道。"""
    from PIL import Image

    transparent = SAMPLE_DIR / "assets" / "transparent.png"
    article = PublishService(db).create_article(
        account_id=account.id,
        title="透明图样本",
        markdown=f"# 透明图\n\n![透明示例]({transparent})\n",
    )
    article.meta = {**(article.meta or {}), "source_path": str(SAMPLE_DIR)}
    db.commit()

    PublishService(db).create_wechat_draft(article.id, upload_inline_images=True)

    assert mock.uploaded_inline, "透明图应被上传"
    uploaded = Path(mock.uploaded_inline[0])
    assert uploaded.suffix.lower() == ".png"
    with Image.open(uploaded) as img:
        assert img.mode == "RGBA", "透明通道应在准备过程中保留"


def test_html_ingress_is_never_markdown_rerendered(db, account, mock, monkeypatch):
    """R03：HTML 一等入口——渲染函数被调用即失败；内容次序与样式保留。"""
    from qiao_wechat.services.renderer import WeChatMarkdownRenderer as R

    def forbidden_render(self, markdown_text, *, title=None):
        raise AssertionError("HTML 入口不得调用 Markdown 渲染")

    monkeypatch.setattr(R, "render", forbidden_render)

    service = PublishService(db)
    article = service.create_html_file_draft(
        account_id=account.id,
        html_path=str(SAMPLE_DIR / "sample_full.html"),
        title="外部 HTML 排版样本",
        cover_source=str(SAMPLE_DIR / "assets" / "cover.png"),
    )

    assert article.meta.get("content_ingress") == "html"
    assert article.theme == "html"
    html = article.html
    # 上游排版意图保留：head 样式表已内联（引用边框颜色）、内容次序不变
    assert "border-left:4px solid #07c160" in html, "head 样式表的类样式应被内联"
    assert html.find("<h1") < html.find("<blockquote") < html.find("<table") < html.find("<pre"), "内容次序应保留"
    # 相对图片已按 asset_base_dir 解析并上传
    assert "mmbiz.qpic.cn/mock/inline/transparent.png" in html
    # 草稿 payload 保持外部 HTML 原文（不经 Markdown 主题包裹）
    payload = mock.draft_payloads[0]
    assert "demo-quote" in payload["content"]
    assert "wemd" not in payload["content"], "草稿内容不得出现 Markdown 主题标记"
    # 本地预览文件生成且不重渲染
    assert article.local_preview_path and Path(article.local_preview_path).exists()


def test_html_string_and_file_entries_produce_same_draft(db, account, mock):
    """同一 HTML 走字符串入口与文件入口：revision 一致，且第二次命中幂等复用。"""
    service = PublishService(db)
    via_string = service.create_html_draft(
        account_id=account.id,
        title="外部 HTML 排版样本",
        html=SAMPLE_HTML,
        asset_base_dir=str(SAMPLE_DIR),
        cover_source=str(SAMPLE_DIR / "assets" / "cover.png"),
    )
    via_file = service.create_html_file_draft(
        account_id=account.id,
        html_path=str(SAMPLE_DIR / "sample_full.html"),
        title="外部 HTML 排版样本",
        cover_source=str(SAMPLE_DIR / "assets" / "cover.png"),
    )
    assert via_string.html == via_file.html
    assert via_string.meta.get("content_revision") == via_file.meta.get("content_revision")
    # 相同内容修订 + 同账号 → 第二次请求复用已录得的草稿，不重复创建远端草稿
    assert len(mock.draft_payloads) == 1
    assert via_file.wx_draft_media_id == via_string.wx_draft_media_id
    assert via_file.meta.get("draft_outcome") == "reused"


def test_preview_html_is_selfcontained_with_local_images(db, account, mock):
    """本地预览：相对图片物化到预览目录，不依赖上传。"""
    md_path = SAMPLE_DIR / "sample_full.md"
    article = PublishService(db).create_article(
        account_id=account.id,
        title="排版回归样本：全要素覆盖",
        markdown=md_path.read_text(encoding="utf-8"),
    )
    article.meta = {**(article.meta or {}), "source_path": str(md_path)}
    db.commit()

    service = PublishService(db)
    rendered = service.render_article(article.id, upload_inline_images=False)

    preview = Path(rendered.local_preview_path)
    assert preview.exists()
    preview_html = preview.read_text(encoding="utf-8")
    soup = BeautifulSoup(preview_html, "html.parser")
    local_imgs = [
        img for img in soup.find_all("img")
        if (img.get("src") or "").startswith("assets/")
    ]
    assert local_imgs, "相对图片应被物化到预览 assets 目录"
    for img in local_imgs:
        assert (preview.parent / img["src"]).is_file(), f"预览图片缺失: {img['src']}"
    # article.html 本身不被改写：保留原始相对路径（上传前状态），
    # 而预览文件里的 src 已替换为物化后的哈希文件名
    assert 'src="assets/transparent.png"' in (rendered.html or "")
    preview_srcs = {img["src"] for img in local_imgs}
    assert all(src != "assets/transparent.png" for src in preview_srcs)


def test_draft_payload_respects_wechat_limits(db, account, mock):
    """提交载荷：标题截断 32、自动摘要、内容上限。"""
    article = PublishService(db).create_article(
        account_id=account.id,
        title="一个超过三十二个字的标题会被截断到微信限制的长度位置" * 1,
        markdown="# 长文\n\n" + "段落内容。" * 200,
    )
    article.meta = {**(article.meta or {}), "source_path": str(SAMPLE_DIR)}
    db.commit()
    PublishService(db).create_wechat_draft(article.id)

    payload = mock.draft_payloads[0]
    assert len(payload["title"]) <= 32
    assert payload["digest"], "应自动生成摘要"
    assert len(payload["content"]) < 20_000
    assert payload["thumb_media_id"] == "DEFAULT_COVER"
