from __future__ import annotations

import re
from html import escape

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
        if head_rules:
            html = self._inline_external_css(html, head_rules)
        return html

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
            selected: dict[str, tuple[tuple, str]] = {}
            for specificity, order, pseudo, payload in matcher.match(wrapped):
                if pseudo is not None:
                    continue
                source_order, declarations = payload
                for prop, value, important in declarations:
                    rank = (1 if important else 0, (0, *specificity), source_order)
                    current = selected.get(prop)
                    if current is None or rank >= current[0]:
                        selected[prop] = (rank, value)
            existing = tinycss2.parse_declaration_list(etree_element.get("style") or "", skip_comments=True, skip_whitespace=True)
            for inline_order, declaration in enumerate(existing):
                if declaration.type != "declaration":
                    continue
                prop = declaration.name if declaration.name.startswith("--") else declaration.lower_name
                rank = (1 if declaration.important else 0, (1, 0, 0, 0), inline_order)
                current = selected.get(prop)
                if current is None or rank >= current[0]:
                    selected[prop] = (rank, tinycss2.serialize(declaration.value).strip())
            merged = {prop: value + ("!important" if rank[0] else "") for prop, (rank, value) in selected.items()}
            if merged:
                etree_element.set("style", self._normalize_style(";".join(f"{k}:{v}" for k, v in merged.items())))
        return escape(tree.text or "") + "".join(
            lxml_html.tostring(child, encoding="unicode", method="html")
            for child in tree
        )

    def compact(self, content: str) -> str:
        # Inline class-based styles before removing editor attributes. Never apply
        # whole-document whitespace regexes: pre/code and inline gaps are text.
        soup = BeautifulSoup(self.normalize(content), "html.parser")

        for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
            comment.extract()

        for tag in soup.find_all(True):
            if tag.name == "img":
                self._ensure_img_src(tag)
                self._img_dimensions_to_style(tag)

            for attr in list(tag.attrs):
                if attr == "style":
                    continue
                retained_data = {"data-wechat-references", "data-wechat-list-marker", "data-role",
                                 "data-wechat-template", "data-wechat-template-revision"}
                if attr in {"class", "id"} or (attr.startswith("data-") and attr not in retained_data
                                               and not attr.startswith("data-format")):
                    del tag.attrs[attr]

            style = tag.attrs.get("style")
            if style is None:
                continue
            if isinstance(style, list):
                style = " ".join(str(part) for part in style)
            normalized = self._normalize_style(str(style), compact=True)
            if normalized:
                tag.attrs["style"] = normalized
            else:
                del tag.attrs["style"]

        self._prune_inherited_styles(soup)
        return str(soup)

    @staticmethod
    def _prune_inherited_styles(soup: BeautifulSoup) -> None:
        """Remove identical inherited declarations across neutral HTML tags only.

        Headings, links and code reset inference because browser default styles
        may intervene. Relative values, important declarations and font/all
        shorthand also stop inference; the optimizer never guesses a computed
        style or changes source text.
        """
        neutral = {"p", "div", "section", "article", "span"}
        properties = {"color", "font-family", "line-height"}

        def known_value(name: str, value: str) -> bool:
            lowered = value.lower()
            if re.search(r"\b(?:inherit|initial|unset|revert|revert-layer|currentcolor)\b|(?:var|calc|env)\s*\(", lowered):
                return False
            if name == "line-height":
                return bool(re.fullmatch(r"(?:\d+(?:\.\d+)?|\.\d+)", value))
            if name == "color":
                return bool(re.fullmatch(r"#[0-9a-fA-F]{3,8}|[a-zA-Z]+|(?:rgb|rgba|hsl|hsla)\([0-9.,% /+-]+\)", value))
            return bool(value)

        def walk(parent, inherited: dict[str, str]) -> None:
            for node in parent.find_all(True, recursive=False):
                if node.name not in neutral:
                    walk(node, {})
                    continue
                declarations = tinycss2.parse_declaration_list(str(node.get("style") or ""), skip_comments=True, skip_whitespace=True)
                if any(item.type != "declaration" or item.lower_name in {"all", "font"} for item in declarations):
                    walk(node, {})
                    continue
                grouped: dict[str, list] = {}
                for item in declarations:
                    grouped.setdefault(item.lower_name, []).append(item)
                effective = dict(inherited)
                removed = set()
                for name in properties:
                    own = grouped.get(name)
                    if not own:
                        continue
                    if len(own) != 1 or own[0].important:
                        effective.pop(name, None)
                        continue
                    value = tinycss2.serialize(own[0].value).strip()
                    if not known_value(name, value):
                        effective.pop(name, None)
                        continue
                    if inherited.get(name) == value:
                        removed.add(name)
                    effective[name] = value
                if removed:
                    retained = [item for item in declarations if item.lower_name not in removed]
                    style = ";".join(
                        f"{item.name}:{tinycss2.serialize(item.value).strip()}" + ("!important" if item.important else "")
                        for item in retained
                    )
                    if style:
                        node["style"] = style
                    else:
                        node.attrs.pop("style", None)
                walk(node, effective)

        walk(soup, {})

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
        for declaration in tinycss2.parse_declaration_list(style, skip_comments=True, skip_whitespace=True):
            if declaration.type != "declaration":
                continue
            key = declaration.name if declaration.name.startswith("--") else declaration.lower_name
            normalized_value = tinycss2.serialize(declaration.value).strip()
            if declaration.important:
                normalized_value += "!important"
            if normalized_value:
                mapping[key] = normalized_value
        return mapping

    @staticmethod
    def _normalize_style(style: str, *, compact: bool = False) -> str:
        declarations = []
        for declaration in tinycss2.parse_declaration_list(style, skip_comments=True, skip_whitespace=True):
            if declaration.type != "declaration":
                continue
            name = declaration.name if declaration.name.startswith("--") else declaration.lower_name
            if compact and name in {"color", "background-color", "border-color", "border", "outline"}:
                for token in declaration.value:
                    if token.type == "hash" and re.fullmatch(r"([0-9a-fA-F])\1([0-9a-fA-F])\2([0-9a-fA-F])\3", token.value):
                        token.value = token.value[::2]
            value = tinycss2.serialize(declaration.value).strip()
            if value:
                declarations.append(f"{name}:{value}" + ("!important" if declaration.important else ""))
        return ";".join(declarations)
