import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import qiao_wechat.cli as cli
from qiao_wechat.db import Base
from qiao_wechat.models import Article, WeChatAccount
from qiao_wechat.services.publisher import PublishService
from qiao_wechat.services.rendered_package import load_rendered_package


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def package(tmp_path):
    source = tmp_path / "confirmed.md"
    source.write_text("# 已确定文章\n\n正文与[来源](https://example.com/)。\n", encoding="utf-8")
    root = tmp_path / "package"
    root.mkdir()
    crop = {"crop_percent_list": [
        {"ratio": ratio, "x1": 0, "y1": 0, "x2": 1, "y2": 1} for ratio in ("2.35_1", "1_1")
    ]}
    files = {
        "article.md": source.read_bytes(), "article.html": b"<section><p>article</p></section>",
        "delivery.html": b"<section><p>bound delivery</p></section>", "cover.png": b"existing cover bytes",
        "cover-info.json": json.dumps(crop).encode(),
    }
    for name, data in files.items():
        (root / name).write_bytes(data)
    report = {
        "source": str(source), "title": "已确定文章", "template": "math-analysis-v1", "revision": 7,
        "source_sha256": digest(files["article.md"]), "rendered_sha256": digest(files["article.html"]),
        "delivery_sha256": digest(files["delivery.html"]),
        "artifacts": {name: digest(data) for name, data in files.items()},
    }
    (root / "package.json").write_text(json.dumps(report), encoding="utf-8")
    return root, source, report


def save_report(root, report):
    (root / "package.json").write_text(json.dumps(report), encoding="utf-8")


def test_old_package_uses_only_bound_html_and_keeps_declared_identity(package):
    root, source, _ = package
    (root / "delivery-optimized.html").write_text("<p>unbound replacement</p>")
    value = load_rendered_package(str(root))
    assert value["html"] == "<section><p>bound delivery</p></section>"
    assert value["source_markdown"] == source.read_text()
    assert value["meta"]["source_path"] == str(source)
    assert value["meta"]["template"] == "math-analysis-v1"
    assert value["meta"]["template_revision"] == 7
    assert value["meta"]["acceptance"] == "user_pending"
    assert "renderer" not in value["meta"]
    assert value["cover_source"] == str(root / "cover.png")
    assert value["meta"]["cover_info"]["crop_percent_list"][0]["x1"] == "0"


def test_new_package_projects_known_renderer_fields_only(package):
    root, _, report = package
    report.update(renderer="qiao_wechat.services.renderer.WeChatMarkdownRenderer",
                  formatter="fixed_math_format.render", template_asset_sha256="a" * 64,
                  native_payload={"private": "must not copy"}, model="unrelated")
    save_report(root, report)
    meta = load_rendered_package(str(root))["meta"]
    assert meta["renderer"] == report["renderer"]
    assert meta["formatter"] == report["formatter"]
    assert meta["template_asset_sha256"] == "a" * 64
    assert "native_payload" not in meta and "model" not in meta


@pytest.mark.parametrize("name", ["article.md", "article.html", "delivery.html", "cover.png", "cover-info.json"])
def test_changed_package_file_is_rejected(package, name):
    root, _, _ = package
    (root / name).write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_rendered_package(str(root))


@pytest.mark.parametrize("key", ["source_sha256", "rendered_sha256", "delivery_sha256"])
def test_changed_manifest_hash_is_rejected(package, key):
    root, _, report = package
    report[key] = "0" * 64
    save_report(root, report)
    with pytest.raises(ValueError, match="hash mismatch"):
        load_rendered_package(str(root))


def test_current_source_must_still_match(package):
    root, source, _ = package
    source.write_text("new authorized version")
    with pytest.raises(ValueError, match="current source hash mismatch"):
        load_rendered_package(str(root))


def test_title_override_cannot_silently_replace_bound_title(package):
    with pytest.raises(ValueError, match="title does not match"):
        load_rendered_package(str(package[0]), title="another title")


@pytest.mark.parametrize("name", ["../outside.md", "/absolute.md"])
def test_manifest_path_escape_is_rejected(package, name):
    root, _, report = package
    report["artifacts"][name] = "a" * 64
    save_report(root, report)
    with pytest.raises(ValueError, match="path escapes"):
        load_rendered_package(str(root))


def test_symlink_artifact_escape_is_rejected(package):
    root, source, report = package
    (root / "outside.md").symlink_to(source)
    report["artifacts"]["outside.md"] = digest(source.read_bytes())
    save_report(root, report)
    with pytest.raises(ValueError, match="path escapes"):
        load_rendered_package(str(root))


def test_cli_package_and_html_are_mutually_exclusive(package):
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["html-draft", "--account-id", "1", "--html", "other.html",
                           "--package-dir", str(package[0])])


@pytest.mark.parametrize("override", ["--cover", "--asset-base-dir"])
def test_cli_rejects_package_override_before_database_or_delivery(package, monkeypatch, override):
    args = cli.build_parser().parse_args(["html-draft", "--account-id", "1", "--package-dir", str(package[0]),
                                         override, "replacement"])
    monkeypatch.setattr(cli, "init_db", lambda: pytest.fail("invalid package must fail before database access"))
    with pytest.raises(SystemExit) as error:
        cli.cmd_html_draft(args)
    assert error.value.code == 1


@pytest.mark.parametrize("packaged", [True, False])
def test_cli_reuses_existing_service_without_a_new_delivery_lifecycle(package, monkeypatch, packaged):
    root, source, _ = package
    calls = []

    class ExistingService:
        def __init__(self, db):
            pass

        def create_html_draft(self, **kwargs):
            calls.append(("html", kwargs))
            return SimpleNamespace(id=1, meta={}, wx_draft_media_id="mock-only")

        def create_html_file_draft(self, **kwargs):
            calls.append(("file", kwargs))
            return SimpleNamespace(id=1, meta={}, wx_draft_media_id="mock-only")

    @contextmanager
    def session():
        yield object()

    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli, "db_session", session)
    monkeypatch.setattr(cli, "PublishService", ExistingService)
    option = ["--package-dir", str(root)] if packaged else ["--html", str(root / "delivery.html")]
    args = cli.build_parser().parse_args(["html-draft", "--account-id", "1", *option, "--no-upload-inline-images"])
    cli.cmd_html_draft(args)
    method, kwargs = calls.pop()
    assert not calls
    assert method == ("html" if packaged else "file")
    assert kwargs["upload_inline_images"] is False
    if packaged:
        assert kwargs["source_markdown"] == source.read_text()
        assert kwargs["html"] == (root / "delivery.html").read_text()
    else:
        assert "source_markdown" not in kwargs


def test_service_creates_markdown_and_html_in_the_same_initial_article(package, monkeypatch):
    root, source, _ = package
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        account = WeChatAccount(name="test", appid="mock", raw_secret="unused")
        db.add(account)
        db.flush()
        service = PublishService(db)
        calls = []

        def prepare(article_id, **kwargs):
            row = db.get(Article, article_id)
            assert row.markdown == source.read_text()
            assert row.html == (root / "delivery.html").read_text()
            calls.append("existing prepare")

        def submit(article_id, **kwargs):
            calls.append("existing submit")
            return db.get(Article, article_id)

        monkeypatch.setattr(service, "prepare_html_article", prepare)
        monkeypatch.setattr(service, "create_wechat_draft", submit)
        article = service.create_html_draft(account_id=account.id, **load_rendered_package(str(root)))
        assert article.meta["content_ingress"] == "html"
        assert calls == ["existing prepare", "existing submit"]


def test_retained_markdown_does_not_hide_changed_html_from_delivery_identity():
    article = Article(account_id=1, title="已确定文章", markdown="同一份源稿", theme="html",
                      html='<section><p style="color:blue">正文</p></section>',
                      meta={"content_ingress": "html"})
    first = PublishService._compute_content_revision(article)
    assert first == PublishService._compute_content_revision(article)
    article.html = '<section><p style="color:green">正文</p><img src="new.png"/></section>'
    assert first != PublishService._compute_content_revision(article)
