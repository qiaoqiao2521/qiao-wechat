"""Conservative WeChat export helpers, independent of draft lifecycle state."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Comment, Doctype, NavigableString, ProcessingInstruction, Tag
import tinycss2

from ..limits import MAX_CONTENT_BYTES, MAX_CONTENT_CHARS

_BLOCKS = {"article", "section", "div", "p", "h1", "h2", "h3", "h4", "h5", "h6",
           "blockquote", "pre", "ul", "ol", "li", "table", "tr", "td", "th", "hr"}
_VISIBLE_URL = re.compile(r'https?://[^\s<>"\u3000]+')


def _safe_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)
                and not parsed.username and not parsed.password
                and not any(ord(c) <= 32 for c in value))
    except ValueError:
        return False


def _visible_urls(soup: BeautifulSoup) -> set[str]:
    urls = set()
    for value in soup.strings:
        if any(_hidden(node) for node in value.parents if isinstance(node, Tag)):
            continue
        text = str(value).strip()
        if _safe_http_url(text):
            urls.add(text)
        urls.update(_VISIBLE_URL.findall(text))
    return urls


def _hidden(node: Tag) -> bool:
    if node.name in {"head", "script", "style", "noscript"} or node.has_attr("hidden"):
        return True
    for declaration in tinycss2.parse_declaration_list(str(node.get("style") or ""), skip_comments=True, skip_whitespace=True):
        if declaration.type != "declaration":
            continue
        value = tinycss2.serialize(declaration.value).strip().lower()
        if declaration.lower_name == "display" and value == "none":
            return True
        if declaration.lower_name == "visibility" and value in {"hidden", "collapse"}:
            return True
    return False


def _preserve_default_link_style(anchor: Tag) -> None:
    """Keep the unvisited link appearance after removing its clickable target.

    Normalization has already inlined supported author CSS. A real link's UA
    color overrides inherited parent color, so merely removing href would turn
    it into ordinary body text. Explicit color/decoration and reset declarations
    remain authoritative; only absent defaults are materialized.
    """
    style = str(anchor.get("style") or "")
    declarations = [item for item in tinycss2.parse_declaration_list(style, skip_comments=True, skip_whitespace=True)
                    if item.type == "declaration"]
    properties = {item.lower_name for item in declarations}
    if "all" in properties:
        return
    defaults = []
    if "color" not in properties:
        defaults.append("color:#0000ee")
    if not {"text-decoration", "text-decoration-line"} & properties:
        defaults.append("text-decoration-line:underline")
    if defaults:
        anchor["style"] = ";".join([style.rstrip(";"), *defaults]).lstrip(";")


def export_source_links(content: str) -> tuple[str, dict]:
    """Keep WeChat links clickable; expose each other HTTP source URL once.

    Link labels and body formatting stay intact. Existing visible full URLs are
    reused, including upstream renderer reference lists. A marked export footer
    makes repeat preparation idempotent and separates derived text from body text.
    """
    soup = BeautifulSoup(content, "html.parser")
    visible = _visible_urls(soup)
    missing: dict[str, str] = {}
    downgraded: list[str] = []
    stripped = 0
    changed = False
    for anchor in soup.find_all("a"):
        href = str(anchor.get("href") or "")
        if not href:
            continue
        safe = _safe_http_url(href)
        parsed = urlsplit(href) if safe else None
        # Compare the actual host, not a prefix such as mp.weixin.qq.com.evil.
        if parsed and parsed.hostname.lower() == "mp.weixin.qq.com" and parsed.scheme.lower() == "https":
            continue
        _preserve_default_link_style(anchor)
        anchor.attrs.pop("href", None)
        anchor.attrs.pop("target", None)
        anchor.attrs.pop("rel", None)
        changed = True
        if safe:
            if href not in downgraded:
                downgraded.append(href)
            if href not in visible:
                missing.setdefault(href, anchor.get_text(" ", strip=True) or "来源")
        else:
            stripped += 1
    if missing:
        footer = soup.new_tag("section")
        footer["data-wechat-references"] = "1"
        footer["style"] = "margin-top:24px;font-size:14px;line-height:1.7;color:#667085"
        heading = soup.new_tag("p", style="margin:0 0 8px;font-weight:700")
        heading.string = "来源链接"
        footer.append(heading)
        for index, (url, label) in enumerate(missing.items(), 1):
            item = soup.new_tag("p", style="margin:0 0 8px;word-break:break-all")
            item.append(f"[{index}] {label}")
            item.append(soup.new_tag("br"))
            item.append(url)
            footer.append(item)
        (soup.body or soup).append(footer)
        changed = True
    return (str(soup) if changed else content), {
        "external_urls": downgraded, "footnotes_added": len(missing), "unsafe_links_removed": stripped,
    }


def semantic_snapshot(content: str) -> dict:
    """Capture source text, exact code whitespace and source URL identities."""
    soup = BeautifulSoup(content, "html.parser")
    image_count = len([node for node in soup.find_all("img") if not node.find_parent("svg")]) + len([
        node for node in soup.find_all("svg") if not node.find_parent("svg")
    ])
    for node in soup.find_all(["head", "script", "style", "noscript", "svg"]):
        node.decompose()
    for node in soup.select("[data-wechat-references], [data-wechat-list-marker]"):
        node.decompose()
    code = [node.get_text() for node in soup.find_all(["pre", "code"])]
    parts = []
    for node in soup.descendants:
        if not isinstance(node, NavigableString) or isinstance(node, (Comment, Doctype, ProcessingInstruction)):
            continue
        value = str(node)
        if not value.strip():
            prev, following = node.previous_sibling, node.next_sibling
            if (getattr(node.parent, "name", None) in {"ul", "ol", "table", "tr"}
                or (prev is None or isinstance(prev, Tag) and prev.name in _BLOCKS)
                or (following is None or isinstance(following, Tag) and following.name in _BLOCKS)):
                continue
        parts.append(value)
    text = re.sub(r"\s+", " ", "".join(parts)).strip()
    links = sorted({str(node["href"]) for node in soup.find_all("a", href=True)
                    if _safe_http_url(str(node["href"]))})
    return {"text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "code_sha256": hashlib.sha256(json.dumps(code, ensure_ascii=False).encode("utf-8")).hexdigest(),
            "code_blocks": len(code), "source_urls": links, "image_count": image_count}


def validate_preserved(source: dict, exported: str) -> dict:
    final = semantic_snapshot(exported)
    if source["text_sha256"] != final["text_sha256"]:
        raise ValueError("WeChat export changed source body text or meaningful inline spacing")
    if source["code_sha256"] != final["code_sha256"]:
        raise ValueError("WeChat export changed source pre/code text, indentation or line breaks")
    if source["image_count"] != final["image_count"]:
        raise ValueError("WeChat export changed the number of source images")
    soup = BeautifulSoup(exported, "html.parser")
    retained = _visible_urls(soup) | set(final["source_urls"])
    lost = [url for url in source["source_urls"] if url not in retained]
    if lost:
        raise ValueError(f"WeChat export lost {len(lost)} full source URL(s)")
    return {"verified": True, "body_sha256": source["text_sha256"],
            "code_blocks": source["code_blocks"], "source_urls": len(source["source_urls"]),
            "image_count": source["image_count"]}


def html_budget(content: str) -> dict:
    """Account for serialized bytes/chars without claiming a server word count."""
    soup = BeautifulSoup(content, "html.parser")
    references = "".join(str(node) for node in soup.select("[data-wechat-references]"))
    for node in soup.select("[data-wechat-references]"):
        node.decompose()
    body = "".join(str(node) for node in soup.descendants if isinstance(node, NavigableString)
                   and not isinstance(node, Comment))
    styles = "".join(str(node.get("style", "")) for node in soup.find_all(True))
    chars, size = len(content), len(content.encode("utf-8"))
    # Count style values separately; attribute names and quotes remain structure.
    text_chars = len(body)
    style_chars = len(styles)
    reference_chars = len(references)
    text_bytes, style_bytes, reference_bytes = (len(value.encode("utf-8")) for value in (body, styles, references))
    return {"chars": chars, "utf8_bytes": size,
            "max_chars_exclusive": MAX_CONTENT_CHARS, "max_bytes_exclusive": MAX_CONTENT_BYTES,
            "remaining_chars": MAX_CONTENT_CHARS - 1 - chars,
            "remaining_utf8_bytes": MAX_CONTENT_BYTES - 1 - size,
            "parts_chars": {"body": text_chars, "styles": style_chars,
                            "references": reference_chars,
                            "structure": chars - text_chars - style_chars - reference_chars},
            "parts_utf8_bytes": {"body": text_bytes, "styles": style_bytes,
                                 "references": reference_bytes,
                                 "structure": size - text_bytes - style_bytes - reference_bytes}}
