from __future__ import annotations

import re

import tinycss2
from bs4 import BeautifulSoup, Comment
from cssselect2 import Matcher, compile_selector_list
from lxml import html as lxml_html


_IMG_SRC_CANDIDATES = ("src", "data-src", "data-original-src", "data-url")


def _wrap_html_tree(tree: lxml_html.HtmlElement):
    """cssselect2 wrapper over an lxml tree; tolerant of odd fragment roots."""
    from cssselect2 import ElementWrapper

    try:
        return ElementWrapper.from_html_root(tree)
    except Exception:
        return None


class HtmlNormalizer:
    def normalize(self, content: str) -> str:
        soup = BeautifulSoup(content, "html.parser")

        # 微信不支持 <style> 标签；head 里的类样式先提取为规则，
        # 在最后一步按选择器内联到元素 style 上，避免上游排版意图被静默丢弃。
        head_rules = self._collect_style_rules(soup)

        for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
            comment.extract()

        for tag in soup.find_all(["script", "noscript", "style"]):
            tag.decompose()

        for img in soup.find_all("img"):
            self._ensure_img_src(img)
            self._img_dimensions_to_style(img)

        for tag in soup.find_all(True):
            style = tag.attrs.get("style")
            if style is None:
                continue
            if isinstance(style, list):
                style = " ".join(str(part) for part in style)
            normalized = self._normalize_style(str(style))
            if normalized:
                tag.attrs["style"] = normalized
            else:
                del tag.attrs["style"]

        root = soup.body if soup.body else soup
        html = "".join(str(node) for node in root.contents)
        html = re.sub(r">\s+<", "><", html)
        if head_rules:
            html = self._inline_external_css(html, head_rules)
        return html.strip()

    def _collect_style_rules(self, soup: BeautifulSoup) -> list[tuple[str, list[tuple[str, str, bool]]]]:
        """Extract (selector, [(prop, value, important)]) rules from <style> tags."""
        rules: list[tuple[str, list[tuple[str, str, bool]]]] = []
        for style_tag in soup.find_all("style"):
            stylesheet = tinycss2.parse_stylesheet(
                style_tag.get_text(),
                skip_comments=True,
                skip_whitespace=True,
            )
            for node in stylesheet:
                if node.type != "qualified-rule":
                    continue
                selector_text = tinycss2.serialize(node.prelude).strip()
                if not selector_text:
                    continue
                declarations: list[tuple[str, str, bool]] = []
                for item in tinycss2.parse_declaration_list(node.content, skip_comments=True, skip_whitespace=True):
                    if item.type != "declaration":
                        continue
                    value = tinycss2.serialize(item.value).strip()
                    if value:
                        declarations.append((item.lower_name, value, item.important))
                if declarations:
                    rules.append((selector_text, declarations))
        return rules

    def _inline_external_css(self, html: str, rules: list[tuple[str, list[tuple[str, str, bool]]]]) -> str:
        """Apply head-derived CSS rules as inline styles; existing inline style wins."""
        matcher = Matcher()
        for order, (selector_text, declarations) in enumerate(rules):
            try:
                selectors = compile_selector_list(selector_text)
            except Exception:
                continue
            for selector in selectors:
                matcher.add_selector(selector, (order, declarations))

        tree = lxml_html.fragment_fromstring(html, create_parent=True)
        wrapper_root = _wrap_html_tree(tree)
        if wrapper_root is None:
            return html
        for wrapped in wrapper_root.iter_subtree():
            etree_element = wrapped.etree_element
            selected: dict[str, tuple[bool, int, str]] = {}
            for specificity, order, pseudo, payload in matcher.match(wrapped):
                if pseudo is not None:
                    continue
                _, declarations = payload
                for prop, value, important in declarations:
                    rank = (1 if important else 0, order)
                    current = selected.get(prop)
                    if current is None or rank >= current[:2]:
                        selected[prop] = (rank[0], rank[1], value)
            if not selected:
                continue
            existing = self._parse_style_map(etree_element.get("style") or "")
            merged = {prop: value for prop, (_, _, value) in selected.items() if prop not in existing}
            merged.update(existing)
            if merged:
                etree_element.set("style", self._normalize_style(";".join(f"{k}:{v}" for k, v in merged.items())))
        return "".join(
            lxml_html.tostring(child, encoding="unicode", method="html")
            for child in tree
        )

    def compact(self, content: str) -> str:
        soup = BeautifulSoup(content, "html.parser")

        for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
            comment.extract()

        for tag in soup.find_all(True):
            if tag.name == "img":
                self._ensure_img_src(tag)
                self._img_dimensions_to_style(tag)

            for attr in list(tag.attrs):
                if attr == "style":
                    continue
                if attr in {"class", "id"} or attr.startswith("data-"):
                    del tag.attrs[attr]

            style = tag.attrs.get("style")
            if style is None:
                continue
            if isinstance(style, list):
                style = " ".join(str(part) for part in style)
            normalized = self._normalize_style(str(style))
            if normalized:
                tag.attrs["style"] = normalized
            else:
                del tag.attrs["style"]

        compacted = str(soup)
        compacted = re.sub(r">\s+<", "><", compacted)
        compacted = re.sub(r"\n+", "", compacted)
        compacted = re.sub(r"\s{2,}", " ", compacted)
        return compacted.strip()

    @staticmethod
    def _ensure_img_src(img) -> None:
        src = img.get("src")
        if src:
            return
        for candidate in _IMG_SRC_CANDIDATES[1:]:
            value = img.get(candidate)
            if value:
                img["src"] = value
                return

    def _img_dimensions_to_style(self, img) -> None:
        style = img.get("style")
        if isinstance(style, list):
            style = " ".join(str(part) for part in style)
        style_map = self._parse_style_map(str(style or ""))

        width = img.get("width")
        height = img.get("height")
        if width and "width" not in style_map:
            style_map["width"] = self._normalize_dimension(str(width))
        if height and "height" not in style_map:
            style_map["height"] = self._normalize_dimension(str(height))
        if width is not None:
            del img.attrs["width"]
        if height is not None:
            del img.attrs["height"]

        if style_map:
            img["style"] = self._normalize_style(";".join(f"{key}:{value}" for key, value in style_map.items()))

    @staticmethod
    def _normalize_dimension(value: str) -> str:
        normalized = value.strip()
        if re.fullmatch(r"\d+", normalized):
            return f"{normalized}px"
        return normalized

    @staticmethod
    def _parse_style_map(style: str) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for declaration in style.split(";"):
            if ":" not in declaration:
                continue
            name, value = declaration.split(":", 1)
            key = name.strip().lower()
            if not key:
                continue
            normalized_value = value.strip()
            if normalized_value:
                mapping[key] = normalized_value
        return mapping

    @staticmethod
    def _normalize_style(style: str) -> str:
        style = re.sub(r"\s*;\s*", ";", style.strip())
        style = re.sub(r"\s*:\s*", ":", style)
        style = re.sub(r";+$", "", style)
        return style
