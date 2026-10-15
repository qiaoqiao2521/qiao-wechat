"""Read a hash-bound render package without owning rendering or delivery."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..limits import normalize_cover_info


def load_rendered_package(directory: str, *, title: str | None = None) -> dict[str, Any]:
    root = Path(directory).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("render package must be a directory")

    def read(name: str) -> bytes:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("render package artifact path escapes its directory")
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("render package artifact path escapes its directory")
        return path.read_bytes()

    def checked(data: bytes, expected: Any, label: str) -> str:
        actual = hashlib.sha256(data).hexdigest()
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected) or actual != expected:
            raise ValueError(f"render package {label} hash mismatch")
        return actual

    manifest = read("package.json")
    report = json.loads(manifest)
    if not isinstance(report, dict):
        raise ValueError("render package manifest must be an object")
    package_title = report.get("title")
    if not isinstance(package_title, str) or not package_title.strip() or (title is not None and title != package_title):
        raise ValueError("render package title does not match")
    template, revision = report.get("template"), report.get("revision")
    if not isinstance(template, str) or not template.strip() or type(revision) is not int or revision < 1:
        raise ValueError("render package template and revision are required")
    artifacts = report.get("artifacts")
    required = {"article.md", "article.html", "delivery.html", "cover.png", "cover-info.json"}
    if not isinstance(artifacts, dict) or not required.issubset(artifacts):
        raise ValueError("render package is missing bound article or cover artifacts")
    contents = {}
    for name, expected in artifacts.items():
        if not isinstance(name, str):
            raise ValueError("render package artifact names must be paths")
        data = read(name)
        checked(data, expected, name)
        contents[name] = data
    checked(contents["article.md"], report.get("source_sha256"), "source")
    checked(contents["article.html"], report.get("rendered_sha256"), "rendered HTML")
    checked(contents["delivery.html"], report.get("delivery_sha256"), "delivery HTML")
    source = report.get("source")
    if not isinstance(source, str) or not Path(source).is_absolute() or Path(source).suffix.lower() != ".md":
        raise ValueError("render package source must be an absolute Markdown path")
    source_path = Path(source).resolve(strict=True)
    checked(source_path.read_bytes(), report["source_sha256"], "current source")
    cover_info = normalize_cover_info(json.loads(contents["cover-info.json"]))
    if cover_info is None:
        raise ValueError("render package cover-info is required")
    identity = {}
    for key in ("renderer", "formatter", "template_asset_sha256"):
        if key in report:
            value = report[key]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"render package {key} must be a nonempty string")
            if key.endswith("sha256") and not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"render package {key} must be a SHA256")
            identity[key] = value
    meta = {
        "delivery_route": "bound_rendered_package", "source_path": str(source_path),
        "source_html_path": str(root / "delivery.html"), "template": template,
        "template_revision": revision, "cover_info": cover_info, "acceptance": "user_pending", **identity,
        "render_package": {
            "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
            **{key: report[key] for key in ("source_sha256", "rendered_sha256", "delivery_sha256")},
            "artifact_hashes": artifacts,
        },
    }
    return {
        "title": package_title, "html": contents["delivery.html"].decode("utf-8"),
        "source_markdown": contents["article.md"].decode("utf-8"),
        "cover_source": str(root / "cover.png"), "asset_base_dir": str(root), "meta": meta,
    }
