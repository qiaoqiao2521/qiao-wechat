from __future__ import annotations

import html
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

import markdown as md
import tinycss2
from bs4 import BeautifulSoup, Tag
from cssselect2 import ElementWrapper, Matcher, compile_selector_list
from lxml import html as lxml_html

from .obsidian_assets import ObsidianAssetAdapter
from .visual_renderer import WeChatVisualRenderer

_THEME_DIR = Path(__file__).resolve().parent.parent / "themes"
_DEFAULT_THEME = "wechat_baseline"
_EXTERNAL_LINK_RE = re.compile(r"^https?://", re.I)
_CALLOUT_RE = re.compile(r"^>\s*\[!([A-Z][A-Z0-9_-]*)\]\s*(.*)$", re.I)
_FRONTMATTER_RE = re.compile(r"(?s)\A---\s*\n.*?\n(?:---|\.\.\.)\s*(?:\n|$)")
_CALLOUT_TITLES = {
    "note": "说明",
    "tip": "提示",
    "important": "重点",
    "warning": "注意",
    "caution": "提醒",
    "summary": "摘要",
}
_VAR_RE = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^)]+))?\)")
_INHERITABLE_PROPERTIES = {
    "color",
    "font",
    "font-family",
    "font-size",
    "font-style",
    "font-variant",
    "font-weight",
    "letter-spacing",
    "line-height",
    "text-align",
    "text-indent",
    "text-transform",
    "visibility",
    "white-space",
    "word-break",
    "word-spacing",
    "overflow-wrap",
    "list-style",
    "list-style-type",
    "list-style-position",
    "cursor",
}
_DROPPED_INLINE_PROPERTIES = {
    "box-sizing",
}


@dataclass(frozen=True)
class Declaration:
    name: str
    value: str
    important: bool
    order: int


@lru_cache(maxsize=1)
def _theme_paths() -> dict[str, Path]:
    return {path.stem: path for path in _THEME_DIR.glob("*.css")}


@lru_cache(maxsize=32)
def _theme_css(theme_name: str) -> str:
    paths = _theme_paths()
    try:
        return paths[theme_name].read_text(encoding="utf-8")
    except KeyError as exc:
        available = ", ".join(sorted(paths))
        raise ValueError(f"Unknown theme: {theme_name}. Available: {available}") from exc


@lru_cache(maxsize=32)
def _theme_matcher(theme_name: str) -> Matcher:
    matcher = Matcher()
    stylesheet = tinycss2.parse_stylesheet(
        _theme_css(theme_name),
        skip_comments=True,
        skip_whitespace=True,
    )
    rule_order = 0
    for node in stylesheet:
        if node.type != "qualified-rule":
            continue
        selector_text = tinycss2.serialize(node.prelude).strip()
        if not selector_text:
            continue
        declarations = _parse_declarations(node.content)
        if not declarations:
            continue
        try:
            selectors = compile_selector_list(selector_text)
        except Exception:
            continue
        rule_order += 1
        payload = (rule_order, tuple(declarations))
        for selector in selectors:
            matcher.add_selector(selector, payload)
    return matcher


def refresh_theme_cache() -> None:
    _theme_paths.cache_clear()
    _theme_css.cache_clear()
    _theme_matcher.cache_clear()


def _parse_declarations(tokens: list[object] | str) -> list[Declaration]:
    declarations: list[Declaration] = []
    parsed = tinycss2.parse_declaration_list(
        tokens,
        skip_comments=True,
        skip_whitespace=True,
    )
    for order, item in enumerate(parsed):
        if item.type != "declaration":
            continue
        value = tinycss2.serialize(item.value).strip()
        if not value:
            continue
        declarations.append(
            Declaration(
                name=item.lower_name,
                value=value,
                important=item.important,
                order=order,
            )
        )
    return declarations


class WeChatMarkdownRenderer:
    def __init__(
        self,
        theme_name: str = _DEFAULT_THEME,
        external_links_as_footnotes: bool = True,
        include_toc: bool = False,
    ):
        _theme_css(theme_name)
        self.theme_name = theme_name
        self.external_links_as_footnotes = external_links_as_footnotes
        self.include_toc = include_toc
        self.visual_renderer = WeChatVisualRenderer()
        self.obsidian_assets = ObsidianAssetAdapter()

    @staticmethod
    def available_themes() -> list[str]:
        return sorted(_theme_paths())

    def render(self, markdown_text: str, *, title: str | None = None) -> str:
        prepared = self._strip_frontmatter(markdown_text)
        prepared = self._preprocess_callouts(prepared)
        prepared = self.obsidian_assets.rewrite_image_embeds(prepared)
        prepared = self.visual_renderer.transform_markdown(prepared)
        body = md.markdown(
            prepared,
            extensions=["extra", "tables", "fenced_code", "sane_lists", "nl2br", "md_in_html"],
            output_format="html5",
        )
        soup = BeautifulSoup(body, "html.parser")
        self._ensure_title(soup, title)
        headings = self._normalize_headings(soup)
        if self.include_toc and headings:
            self._insert_toc(soup, headings)
        if self.external_links_as_footnotes:
            self._links_to_footnotes(soup)
        self._decorate_headings(soup)
        self._decorate_semantic_blocks(soup)

        root = soup.new_tag("section", id="wemd")
        root["class"] = [f"theme-{self.theme_name}"]
        for node in list(soup.contents):
            root.append(node)
        return self._inline_theme(root)

    def save_preview(self, html_body: str, out_path: Path, *, page_title: str = "Preview") -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{html.escape(page_title)}</title>
<style>
body {{ margin:0; background:#f3f4f6; }}
.phone-stage {{ padding:24px 0; }}
.preview-shell {{ max-width:430px; margin:0 auto; background:#fff; min-height:100vh; box-shadow:0 12px 40px rgba(0,0,0,.12); }}
.wechat-body {{ padding:20px 16px 28px; }}
@media (max-width: 520px) {{
  .phone-stage {{ padding:0; }}
  .preview-shell {{ max-width:none; box-shadow:none; }}
  .wechat-body {{ padding:18px 14px 24px; }}
}}
/* 深色模拟：近似读者在微信深色模式下看到的效果。反相 + 色相旋转近似微信的暗色映射，
   图片/图表再反相回来保持原色。这是预览模拟，不代表微信的精确渲染。 */
.preview-shell.dark-sim {{ background:#111; filter: invert(0.92) hue-rotate(180deg); }}
.preview-shell.dark-sim img,
.preview-shell.dark-sim svg,
.preview-shell.dark-sim video {{ filter: invert(1) hue-rotate(180deg); }}
.dark-toggle {{
  position: fixed; top: 12px; right: 12px; z-index: 10;
  display: flex; gap: 0; border-radius: 8px; overflow: hidden;
  box-shadow: 0 2px 10px rgba(0,0,0,.18); font-size: 12px;
}}
.dark-toggle button {{
  border: 0; padding: 6px 10px; cursor: pointer; background: #fff; color: #333; font-weight: 400;
}}
.dark-toggle button.active {{ background: #2563eb; color: #fff; }}
</style>
</head>
<body>
<div class="dark-toggle" id="dark-toggle" title="模拟读者微信深色模式（近似效果）">
  <button data-mode="auto" class="active">自动</button>
  <button data-mode="light">浅色</button>
  <button data-mode="dark">深色(模拟)</button>
</div>
<div class="phone-stage"><main class="preview-shell"><article class="wechat-body">{html_body}</article></main></div>
<script>
(function () {{
  var shell = document.querySelector('.preview-shell');
  var buttons = document.querySelectorAll('#dark-toggle button');
  var saved = null;
  try {{ saved = localStorage.getItem('wemd-preview-dark'); }} catch (e) {{}}
  function apply(mode) {{
    var dark;
    if (mode === 'dark') dark = true;
    else if (mode === 'light') dark = false;
    else dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    shell.classList.toggle('dark-sim', dark);
    buttons.forEach(function (b) {{ b.classList.toggle('active', b.dataset.mode === mode); }});
  }}
  buttons.forEach(function (b) {{
    b.addEventListener('click', function () {{
      apply(b.dataset.mode);
      try {{ localStorage.setItem('wemd-preview-dark', b.dataset.mode); }} catch (e) {{}}
    }});
  }});
  apply(saved || 'auto');
}})();
</script>
</body>
</html>"""
        out_path.write_text(document, encoding="utf-8")
        return out_path

    def replace_image_sources(self, html_body: str, uploader: Callable[[str], str]) -> str:
        soup = BeautifulSoup(html_body, "html.parser")
        for img in soup.find_all("img"):
            src = img.get("src")
            if not src or src.startswith(("data:", "https://mmbiz.qpic.cn/", "http://mmbiz.qpic.cn/")):
                continue
            img["data-original-src"] = src
            img["src"] = uploader(src)
        return str(soup)

    def _strip_frontmatter(self, text: str) -> str:
        return _FRONTMATTER_RE.sub("", text, count=1)

    def _preprocess_callouts(self, text: str) -> str:
        lines = text.splitlines()
        out: list[str] = []
        i = 0
        while i < len(lines):
            match = _CALLOUT_RE.match(lines[i])
            if not match:
                out.append(lines[i])
                i += 1
                continue
            kind = match.group(1).lower()
            title = match.group(2).strip() or _CALLOUT_TITLES.get(kind, kind.upper())
            content: list[str] = []
            i += 1
            while i < len(lines) and lines[i].startswith(">"):
                content.append(re.sub(r"^>\s?", "", lines[i]))
                i += 1
            out.append(
                f'<section class="callout callout-{kind}" data-callout="{kind}" markdown="1">'
                f'<p class="callout-title" data-callout-title="1">{html.escape(title)}</p>'
            )
            out.extend(content)
            out.append("</section>")
        return "\n".join(out)

    def _ensure_title(self, soup: BeautifulSoup, title: str | None) -> None:
        if not title:
            return
        first_heading = soup.find(["h1", "h2"])
        if first_heading and first_heading.get_text(strip=True) == title.strip():
            return
        h1 = soup.new_tag("h1")
        h1.string = title
        soup.insert(0, h1)

    def _decorate_semantic_blocks(self, soup: BeautifulSoup) -> None:
        for section in soup.find_all("section"):
            classes = set(section.get("class", []))
            if section.get("data-callout"):
                classes.update({"callout", f"callout-{section['data-callout']}"})
                section.attrs.pop("markdown", None)
            if section.get("data-toc") == "1":
                classes.add("toc")
            if section.get("data-citations") == "1":
                classes.add("citations")
            if classes:
                section["class"] = sorted(classes)
        for title in soup.find_all(attrs={"data-callout-title": "1"}):
            classes = set(title.get("class", []))
            classes.add("callout-title")
            title["class"] = sorted(classes)

    def _decorate_headings(self, soup: BeautifulSoup) -> None:
        for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
            if heading.find("span", class_="content", recursive=False):
                continue
            content = soup.new_tag("span")
            content["class"] = ["content"]
            nodes = list(heading.contents)
            for node in nodes:
                content.append(node.extract())
            prefix = soup.new_tag("span")
            prefix["class"] = ["prefix"]
            suffix = soup.new_tag("span")
            suffix["class"] = ["suffix"]
            heading.append(prefix)
            heading.append(content)
            heading.append(suffix)

    def _normalize_headings(self, soup: BeautifulSoup) -> list[tuple[str, str, str]]:
        headings: list[tuple[str, str, str]] = []
        seen: dict[str, int] = {}
        for tag in soup.find_all(["h1", "h2", "h3"]):
            text = tag.get_text(" ", strip=True)
            if not text:
                continue
            base = self._slugify(text)
            count = seen.get(base, 0)
            seen[base] = count + 1
            slug = base if count == 0 else f"{base}-{count + 1}"
            tag["id"] = slug
            tag["data-heading"] = tag.name
            headings.append((tag.name, text, slug))
        return headings

    def _insert_toc(self, soup: BeautifulSoup, headings: list[tuple[str, str, str]]) -> None:
        toc = soup.new_tag("section")
        toc["data-toc"] = "1"
        toc["class"] = ["toc"]
        title = soup.new_tag("p")
        title["class"] = ["toc-title"]
        title.string = "目录"
        toc.append(title)
        for level, text, slug in headings:
            p = soup.new_tag("p")
            p["class"] = ["toc-item", f"toc-item-{level}"]
            a = soup.new_tag("a", href=f"#{slug}")
            a.string = text
            p.append(a)
            toc.append(p)
        first = soup.find(["h1", "h2", "h3", "p", "section", "blockquote", "ul", "ol"])
        if first:
            first.insert_before(toc)
        else:
            soup.append(toc)

    def _links_to_footnotes(self, soup: BeautifulSoup) -> None:
        footnotes: list[tuple[str, str]] = []
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if not _EXTERNAL_LINK_RE.match(href):
                continue
            idx = len(footnotes) + 1
            label = a.get_text(strip=True) or href
            footnotes.append((label, href))
            a["href"] = "#ref-" + str(idx)
            a.string = f"{label} [{idx}]"
        if not footnotes:
            return
        box = soup.new_tag("section")
        box["data-citations"] = "1"
        box["class"] = ["citations"]
        title = soup.new_tag("h2")
        title.string = "参考链接"
        box.append(title)
        lst = soup.new_tag("ol")
        for idx, (label, href) in enumerate(footnotes, start=1):
            li = soup.new_tag("li", id=f"ref-{idx}")
            label_node = soup.new_tag("span")
            label_node["class"] = ["citation-label"]
            label_node.string = f"[{idx}] {label}"
            link = soup.new_tag("a", href=href)
            link.string = href
            li.append(label_node)
            li.append(link)
            lst.append(li)
        box.append(lst)
        soup.append(box)

    def _inline_theme(self, root: Tag) -> str:
        element = lxml_html.fromstring(str(root))
        matcher = _theme_matcher(self.theme_name)
        wrapper_root = ElementWrapper.from_html_root(element)
        resolved_vars: dict[object, dict[str, str]] = {}
        resolved_styles: dict[object, dict[str, str]] = {}

        for wrapped in wrapper_root.iter_subtree():
            etree_element = wrapped.etree_element
            parent_element = wrapped.parent.etree_element if wrapped.parent is not None else None
            parent_vars = resolved_vars.get(
                parent_element,
                {},
            )
            parent_styles = resolved_styles.get(parent_element, {})
            selected = self._select_declarations(wrapped, matcher)

            scoped_vars = dict(parent_vars)
            scoped_vars.update({name: value for name, value in selected.items() if name.startswith("--")})
            computed_vars = dict(parent_vars)
            for name, value in scoped_vars.items():
                if not name.startswith("--"):
                    continue
                computed_vars[name] = self._resolve_value(value, scoped_vars)

            rendered: list[tuple[str, str]] = []
            for name, value in selected.items():
                if name.startswith("--"):
                    continue
                if name in _DROPPED_INLINE_PROPERTIES:
                    continue
                resolved = self._resolve_value(value, computed_vars)
                if name in _INHERITABLE_PROPERTIES and parent_styles.get(name) == resolved:
                    continue
                rendered.append((name, resolved))

            if rendered:
                etree_element.set("style", self._serialize_style(rendered))
            else:
                etree_element.attrib.pop("style", None)
            resolved_vars[etree_element] = computed_vars
            resolved_styles[etree_element] = {
                **parent_styles,
                **{name: value for name, value in rendered},
            }

        return lxml_html.tostring(element, encoding="unicode", method="html")

    def _select_declarations(self, wrapped: ElementWrapper, matcher: Matcher) -> dict[str, str]:
        chosen: dict[str, tuple[tuple[object, ...], str]] = {}
        for specificity, order, pseudo, payload in matcher.match(wrapped):
            if pseudo is not None:
                continue
            _, declarations = payload
            for declaration in declarations:
                priority = (
                    1 if declaration.important else 0,
                    0,
                    specificity,
                    order,
                    declaration.order,
                )
                current = chosen.get(declaration.name)
                if current is None or priority >= current[0]:
                    chosen[declaration.name] = (priority, declaration.value)

        inline_style = wrapped.etree_element.get("style", "")
        for declaration in _parse_declarations(inline_style):
            priority = (
                1 if declaration.important else 0,
                1,
                (1, 0, 0),
                10**9,
                declaration.order,
            )
            current = chosen.get(declaration.name)
            if current is None or priority >= current[0]:
                chosen[declaration.name] = (priority, declaration.value)

        return {name: value for name, (_, value) in chosen.items()}

    def _resolve_value(self, value: str, variables: dict[str, str]) -> str:
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            fallback = match.group(2)
            if name in variables:
                candidate = variables[name]
                if candidate == value:
                    return candidate
                return self._resolve_value(candidate, variables)
            if fallback is not None:
                return self._resolve_value(fallback.strip(), variables)
            return ""

        resolved = value
        for _ in range(8):
            updated = _VAR_RE.sub(replace, resolved)
            if updated == resolved:
                break
            resolved = updated
        return re.sub(r"\s+", " ", resolved).strip()

    @staticmethod
    def _serialize_style(declarations: list[tuple[str, str]]) -> str:
        return ";".join(f"{name}:{value}" for name, value in declarations if value) + ";"

    @staticmethod
    def _slugify(text: str) -> str:
        cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text.lower(), flags=re.U).strip("-")
        return cleaned[:80] or "heading"
