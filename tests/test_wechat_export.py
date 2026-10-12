from copy import deepcopy

import pytest
from bs4 import BeautifulSoup
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from qiao_wechat.db import Base
from qiao_wechat.limits import normalize_cover_info, validate_draft_text
from qiao_wechat.models import Article, JobStatus, PublishChannel, PublishJob, WeChatAccount
from qiao_wechat.schemas import ArticleCreate, HtmlDraftCreate, HtmlDraftFileCreate
from qiao_wechat.services.html_normalizer import HtmlNormalizer
from qiao_wechat.services.publisher import PublishService
from qiao_wechat.services.wechat_export import export_source_links, html_budget, semantic_snapshot, validate_preserved


@pytest.fixture
def service(tmp_path, monkeypatch):
    from qiao_wechat.config import Settings
    from qiao_wechat.services import publisher
    settings = Settings(output_dir=tmp_path / "outputs", upload_dir=tmp_path / "uploads")
    monkeypatch.setattr(publisher, "get_settings", lambda: settings)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield PublishService(session)


def test_normalize_and_compact_preserve_code_and_inline_spaces():
    source = '<section><p><strong>hello</strong> <em>world</em></p><pre><code>if x:\n    print(x)\n\n    return x</code></pre></section>'
    expected = semantic_snapshot(source)
    for transformed in (HtmlNormalizer().normalize(source), HtmlNormalizer().compact(source)):
        validate_preserved(expected, transformed)
        assert BeautifulSoup(transformed, "html.parser").code.get_text() == "if x:\n    print(x)\n\n    return x"
        assert "</strong> <em>" in transformed


def test_css_inlining_preserves_root_text_specificity_and_important():
    source = '<head><style>#x{color:red}p{color:blue}.y{color:green!important}</style></head><body>前文<p id="x">正文</p><p class="y" style="color:purple">结尾</p>后文</body>'
    normalized = HtmlNormalizer().normalize(source)
    assert normalized.startswith("前文") and normalized.endswith("后文")
    soup = BeautifulSoup(normalized, "html.parser")
    assert soup.find(id="x")["style"] == "color:red"
    assert soup.find(class_="y")["style"] == "color:green!important"
    validate_preserved(semantic_snapshot(source), normalized)


def test_compact_retains_css_strings_data_roles_and_code_whitespace():
    source = '<p data-role="body" data-format="math-analysis-v1" class="x" style="color:#ffffff;content:\'a:  b; c\';background-image:url(\'https://example.com/a;b.png\')">文字</p>'
    final = HtmlNormalizer().compact(source)
    soup = BeautifulSoup(final, "html.parser")
    assert soup.p["data-role"] == "body" and soup.p["data-format"] == "math-analysis-v1"
    assert "color:#fff" in soup.p["style"]
    assert 'a:  b; c' in soup.p["style"]
    assert 'https://example.com/a;b.png' in soup.p["style"]
    assert len(final) < len(source)


def test_compact_prunes_identical_inherited_styles_only_across_neutral_tags():
    source = '<section style="color:#222;font-family:sans-serif;line-height:1.9"><p style="color:#222;font-family:sans-serif;line-height:1.9;margin:12px">正文<span style="color:#222;line-height:1.9">补充</span></p><h2><span style="color:#222;line-height:1.9">标题</span></h2><pre><code><span style="font-family:sans-serif;line-height:1.9">code</span></code></pre><a><span style="color:#222">链接</span></a></section>'
    final = HtmlNormalizer().compact(source)
    soup = BeautifulSoup(final, "html.parser")
    assert soup.p["style"] == "margin:12px"
    assert "style" not in soup.p.span.attrs
    assert soup.h2.span["style"] == "color:#222;line-height:1.9"
    assert soup.code.span["style"] == "font-family:sans-serif;line-height:1.9"
    assert soup.a.span["style"] == "color:#222"
    validate_preserved(semantic_snapshot(source), final)


@pytest.mark.parametrize("value", ["1.9em", "var(--line-height)", "inherit", "1.9!important"])
def test_relative_unknown_and_important_values_are_not_pruned(value):
    source = f'<section style="line-height:{value}"><p style="line-height:{value}">正文</p></section>'
    final = HtmlNormalizer().compact(source)
    assert BeautifulSoup(final, "html.parser").p.get("style") == f"line-height:{value}"


def test_font_shorthand_stops_inheritance_inference():
    source = '<section style="font-family:sans-serif;line-height:1.9"><div style="font:12px monospace"><p style="font-family:sans-serif;line-height:1.9">正文</p></div></section>'
    assert BeautifulSoup(HtmlNormalizer().compact(source), "html.parser").p["style"] == "font-family:sans-serif;line-height:1.9"


def test_external_sources_deduplicate_and_keep_real_wechat_links():
    source = '<p><a href="https://example.org/report?a=1&amp;b=2"><strong>研究</strong></a> <a href="https://example.org/report?a=1&amp;b=2">再看</a> <a href="https://mp.weixin.qq.com/s/abc">公众号</a> <a href="https://mp.weixin.qq.com.evil.test/x">假前缀</a></p>'
    final, info = export_source_links(source)
    validate_preserved(semantic_snapshot(source), final)
    soup = BeautifulSoup(final, "html.parser")
    assert soup.find("a", href=True)["href"] == "https://mp.weixin.qq.com/s/abc"
    assert len(soup.find_all("a", href=True)) == 1
    assert len(soup.select("[data-wechat-references] p")) == 3
    assert soup.get_text().count("https://example.org/report?a=1&b=2") == 1
    assert info["footnotes_added"] == 2
    assert export_source_links(final)[0] == final


def test_downgraded_default_link_keeps_blue_underline_and_readable_footer():
    source = '<section style="color:#334155"><p><a href="https://example.org/s">来源</a></p></section>'
    final, _ = export_source_links(HtmlNormalizer().normalize(source))
    soup = BeautifulSoup(HtmlNormalizer().compact(final), "html.parser")
    assert soup.a["style"] == "color:#00e;text-decoration-line:underline"
    assert not soup.a.has_attr("href")
    footer = soup.select_one("[data-wechat-references]")
    assert "font-size:14px" in footer["style"] and "line-height:1.7" in footer["style"]
    validate_preserved(semantic_snapshot(source), str(soup))


@pytest.mark.parametrize("style", [
    "color:inherit;text-decoration:none",
    "color:#aabbcc!important;text-decoration-line:none;text-decoration-color:red",
    "all:unset",
])
def test_downgraded_link_respects_explicit_author_styles(style):
    source = f'<section style="color:#334155"><a href="https://example.org/s" style="{style}">来源</a></section>'
    final, _ = export_source_links(HtmlNormalizer().normalize(source))
    assert BeautifulSoup(final, "html.parser").a["style"] == style


def test_downgraded_link_preserves_inlined_css_and_decoration_subproperties():
    source = '<head><style>a{color:inherit;text-decoration-line:none}</style></head><body><section style="color:#334155"><a href="https://example.org/s">来源</a></section></body>'
    final, _ = export_source_links(HtmlNormalizer().normalize(source))
    assert BeautifulSoup(final, "html.parser").a["style"] == "color:inherit;text-decoration-line:none"
    source = '<a href="https://example.org/s" style="text-decoration-color:red">来源</a>'
    final, _ = export_source_links(source)
    style = BeautifulSoup(final, "html.parser").a["style"]
    assert "text-decoration-color:red" in style and "text-decoration-line:underline" in style


def test_existing_visible_url_is_not_duplicated_and_unsafe_schemes_removed():
    source = '<p><a href="https://example.org/s">来源</a></p><p>https://example.org/s</p><p><a href="javascript:alert(1)">按钮</a><a href="file:///etc/passwd">本地</a></p>'
    final, info = export_source_links(source)
    assert info["footnotes_added"] == 0
    assert info["unsafe_links_removed"] == 2
    assert not BeautifulSoup(final, "html.parser").find("a", href=True)
    assert final.count("https://example.org/s") == 1
    validate_preserved(semantic_snapshot(source), final)


@pytest.mark.parametrize("damage,match", [
    (lambda s: s.replace("    print(x)", " print(x)"), "pre/code"),
    (lambda s: s.replace("</strong> <em>", "</strong><em>"), "body text"),
    (lambda s: s.replace("https://example.org/s", ""), "source URL"),
])
def test_source_to_wire_guard_detects_damage(damage, match):
    source = '<p><strong>hello</strong> <em>world</em><a href="https://example.org/s">来源</a></p><pre><code>if x:\n    print(x)</code></pre>'
    final, _ = export_source_links(source)
    with pytest.raises(ValueError, match=match):
        validate_preserved(semantic_snapshot(source), damage(final))


def test_final_budget_includes_added_references_without_truncating(service):
    # Fits before export; full source URL deliberately exceeds the final limit.
    source = '<p>原文<a href="https://example.org/' + "x" * 9_650 + '">来源</a></p>' + "<p>正文</p>" * 1_128
    assert len(source) < 20_000
    article = Article(title="预算", html=source, markdown="", meta={})
    with pytest.raises(ValueError, match="parts_chars=.*references"):
        service._prepare_wechat_draft_content(article, source)
    budget = article.meta["wechat_html_budget"]
    assert budget["parts_chars"]["references"] > 9_000
    assert budget["remaining_chars"] < 0
    assert article.html == source


def test_budget_parts_sum_and_escaped_text_is_nonnegative():
    source = '<p style="color:#222">A &amp; B</p>'
    budget = html_budget(source)
    assert sum(budget["parts_chars"].values()) == len(source)
    assert budget["remaining_chars"] == 19_999 - len(source)
    assert all(value >= 0 for value in budget["parts_chars"].values())
    assert sum(budget["parts_utf8_bytes"].values()) == len(source.encode("utf-8"))


def test_hidden_url_does_not_satisfy_visible_source_requirement():
    source = '<p><a href="https://example.org/s">来源</a></p><p style="display:none">https://example.org/s</p>'
    final, info = export_source_links(source)
    assert info["footnotes_added"] == 1
    assert BeautifulSoup(final, "html.parser").select_one("[data-wechat-references]").get_text().endswith("https://example.org/s")
    validate_preserved(semantic_snapshot(source), final)


def test_lists_compaction_preserve_source_and_are_idempotent(service):
    source = '<section data-format="math-analysis-v1"><p>导语</p>\n<ul>\n<li><strong>一</strong> <em>条</em></li>\n<li>二条<pre><code>x:\n    y</code></pre></li>\n</ul><p><a href="https://example.org/s">来源</a></p></section>'
    article = Article(title="列表", html=source, markdown="", meta={})
    first = service._prepare_wechat_draft_content(article, source)
    original_audit = deepcopy(article.meta["wechat_source_links"])
    second = service._prepare_wechat_draft_content(article, first)
    assert first == second
    assert "data-format" in first
    assert article.meta["wechat_export_preservation"]["verified"]
    assert article.meta["wechat_html_budget"]["chars"] == len(first)
    assert article.meta["wechat_source_links"] == original_audit
    assert article.meta["wechat_source_links"]["external_urls"] == ["https://example.org/s"]


def test_source_fingerprint_and_link_audit_reset_when_preparing_new_approved_html(service):
    article = service.create_article(title="更新", html='<p><a href="https://example.org/old">旧文</a></p>', markdown="", meta={"content_ingress": "html"})
    service.prepare_html_article(article.id, upload_inline_images=False, create_local_preview=False)
    service._prepare_wechat_draft_content(article, article.html)
    assert article.meta["wechat_source_links"]["external_urls"] == ["https://example.org/old"]
    article.html = '<p><a href="https://example.org/new">新文</a></p>'
    service.prepare_html_article(article.id, upload_inline_images=False, create_local_preview=False)
    final = service._prepare_wechat_draft_content(article, article.html)
    assert article.meta["wechat_source_links"]["external_urls"] == ["https://example.org/new"]
    assert "新文" in final and "旧文" not in final


@pytest.mark.parametrize("schema,kwargs", [
    (ArticleCreate, {"markdown": "正文"}),
    (HtmlDraftCreate, {"html": "<p>正文</p>", "account_id": 1}),
    (HtmlDraftFileCreate, {"html_path": "article.html", "account_id": 1}),
])
def test_digest_limit_counts_characters(schema, kwargs):
    assert schema(title="标题", digest="中" * 120, **kwargs).digest == "中" * 120
    with pytest.raises(ValidationError):
        schema(title="标题", digest="中" * 121, **kwargs)
    validate_draft_text("标题", "中" * 120)
    with pytest.raises(ValueError, match="120 characters"):
        validate_draft_text("标题", "中" * 121)


def _cover_info():
    return {"crop_percent_list": [
        {"ratio": "2.35_1", "x1": 0, "y1": "0.0", "x2": "0.7", "y2": 1},
        {"ratio": "1_1", "x1": "0.70", "y1": 0, "x2": 1, "y2": "1.00"},
    ]}


@pytest.mark.parametrize("bad", [
    {"crop_percent_list": []},
    {"crop_percent_list": [{"ratio": "1_1", "x1": 0, "y1": 0, "x2": 1, "y2": 1}] * 2},
    {"crop_percent_list": [{"ratio": "2.35_1", "x1": 0, "y1": 0, "x2": "nan", "y2": 1}, {"ratio": "1_1", "x1": 0, "y1": 0, "x2": 1, "y2": 1}]},
])
def test_invalid_crops_rejected(bad):
    with pytest.raises(ValueError):
        normalize_cover_info(bad)


def test_crop_metadata_changes_request_even_with_cached_content_revision(service):
    article = Article(title="封面", markdown="正文", account_id=1, meta={"content_revision": "cached", "cover_info": _cover_info()})
    account = WeChatAccount(id=1, name="mock", appid="wx-mock")
    original_key = service._draft_request_key(article, account)
    equivalent = deepcopy(_cover_info())
    equivalent["crop_percent_list"].reverse()
    article.meta = {"content_revision": "cached", "cover_info": equivalent}
    assert service._draft_request_key(article, account) == original_key
    changed = deepcopy(_cover_info())
    changed["crop_percent_list"][0]["x2"] = "0.65"
    article.meta = {"content_revision": "cached", "cover_info": changed}
    assert service._draft_request_key(article, account) != original_key


def test_new_draft_payload_preserves_digest_and_crop_metadata(service, monkeypatch):
    from qiao_wechat.services import publisher
    captured = []
    class Client:
        def __init__(self, *args):
            pass
        def add_draft(self, articles):
            captured.extend(articles)
            return {"media_id": "mock-draft"}
    monkeypatch.setattr(publisher, "WeChatApiClient", Client)
    account = WeChatAccount(name="mock", appid="wx-mock", default_cover_media_id="mock-cover")
    service.session.add(account)
    service.session.flush()
    article = service.create_article(account_id=account.id, title="封面", markdown="正文", html="<p>正文</p>", digest="中" * 120, meta={"cover_info": _cover_info()})
    service.create_wechat_draft(article.id, upload_inline_images=False)
    assert captured[0]["digest"] == "中" * 120
    assert captured[0]["cover_info"] == normalize_cover_info(_cover_info())
    assert "微信" not in captured[0]["content"]


def test_invalid_direct_digest_rejects_before_remote_calls(service, monkeypatch):
    from qiao_wechat.services import publisher
    class Client:
        def __init__(self, *args):
            pass
        def add_draft(self, articles):
            pytest.fail("must not call WeChat for an invalid digest")
    monkeypatch.setattr(publisher, "WeChatApiClient", Client)
    account = WeChatAccount(name="mock", appid="wx-mock", default_cover_media_id="mock-cover")
    service.session.add(account)
    service.session.flush()
    article = service.create_article(account_id=account.id, title="摘要", markdown="正文", html="<p>正文</p>", digest="中" * 121)
    with pytest.raises(ValueError, match="120 characters"):
        service.create_wechat_draft(article.id, upload_inline_images=False)


@pytest.mark.parametrize("field", ["digest", "author"])
def test_changed_metadata_requires_new_prepared_version_and_does_not_reuse(service, monkeypatch, field):
    from qiao_wechat.services import publisher
    calls = []
    class Client:
        def __init__(self, *args):
            pass
        def add_draft(self, articles):
            calls.extend(articles)
            return {"media_id": f"draft-{len(calls)}"}
    monkeypatch.setattr(publisher, "WeChatApiClient", Client)
    account = WeChatAccount(name="mock", appid="wx-mock", default_cover_media_id="mock-cover")
    service.session.add(account)
    service.session.flush()
    article = service.create_article(account_id=account.id, title="元数据", html="<p>正文</p>", markdown="", author="作者", digest="摘要", meta={"content_ingress": "html"})
    service.prepare_html_article(article.id, upload_inline_images=False, create_local_preview=False)
    service.create_wechat_draft(article.id, upload_inline_images=False)
    service.create_wechat_draft(article.id, upload_inline_images=False)
    assert len(calls) == 1
    setattr(article, field, "已修订")
    with pytest.raises(ValueError, match="metadata changed"):
        service.create_wechat_draft(article.id, upload_inline_images=False)
    assert len(calls) == 1
    service.prepare_html_article(article.id, upload_inline_images=False, create_local_preview=False)
    service.create_wechat_draft(article.id, upload_inline_images=False)
    assert len(calls) == 2 and calls[-1][field] == "已修订"


def test_legacy_unknown_key_is_not_bypassed_by_crop_aware_key(service, monkeypatch):
    from qiao_wechat.services import publisher
    class Client:
        def __init__(self, *args):
            pass
        def add_draft(self, articles):
            pytest.fail("unknown legacy submission must be reconciled before a new request")
    monkeypatch.setattr(publisher, "WeChatApiClient", Client)
    account = WeChatAccount(name="mock", appid="wx-mock", default_cover_media_id="mock-cover")
    service.session.add(account)
    service.session.flush()
    article = service.create_article(account_id=account.id, title="旧轮次", html="<p>正文</p>", markdown="正文")
    article.meta = {"content_revision": service._compute_content_revision(article)}
    legacy_key = service._legacy_draft_request_key(article, account)
    service.session.add(PublishJob(article_id=article.id, account_id=account.id, channel=PublishChannel.wx_draft,
                                   status=JobStatus.failed, request_key=legacy_key, error_category="remote_result_unknown"))
    service.session.flush()
    article.meta = {**article.meta, "cover_info": _cover_info()}
    assert service._draft_request_key(article, account) != legacy_key
    with pytest.raises(ValueError, match="unknown remote outcome"):
        service.create_wechat_draft(article.id, upload_inline_images=False)
