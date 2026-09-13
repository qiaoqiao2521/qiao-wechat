#!/usr/bin/env python3
"""从小红书排版技能仓库（xiaohu-wechat-format）批量导入主题。

来源主题是 JSON 描述文件（styles 按元素键 + snake_case 属性），本脚本：
1. 把每个 JSON 转成 `#wemd` 命名空间的 CSS（选择器映射见 SELECTOR_MAP）；
2. 强制微信安全换行（pre/pre-code 加 pre-wrap+break-all，去掉 min-width:max-content）；
3. 用 ThemeManager.import_css 落库（名称加 xh- 前缀避免与现有主题冲突）；
4. 每套主题渲染回归样本做结构验证，失败的跳过并在报告中列出。

用法：uv run python scripts/import_external_themes.py <themes-json-dir>
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

SOURCE_REPO = "https://github.com/xiaohuailabs/xiaohu-wechat-format"

# JSON styles 键 → 本仓渲染器 DOM 的 CSS 选择器（根为 section#wemd）
SELECTOR_MAP: dict[str, str] = {
    "wrapper": "#wemd",
    "h1": "#wemd h1",
    "h2": "#wemd h2",
    "h3": "#wemd h3",
    "h4": "#wemd h4",
    "h5": "#wemd h5",
    "h6": "#wemd h6",
    "h1_inner": "#wemd h1 .content",
    "h2_prefix": "#wemd h2 .prefix",
    "h2_inner": "#wemd h2 .content",
    "h3_inner": "#wemd h3 .content",
    "p": "#wemd p",
    "blockquote": "#wemd blockquote",
    "blockquote_p": "#wemd blockquote p",
    "strong": "#wemd strong",
    "em": "#wemd em",
    "a": "#wemd a",
    "img": "#wemd img",
    "img_wrapper": "#wemd figure",
    "hr": "#wemd hr",
    "code": "#wemd code",
    "pre": "#wemd pre",
    "pre_code": "#wemd pre code",
    "table": "#wemd table",
    "th": "#wemd th",
    "td": "#wemd td",
    "list_wrapper": "#wemd ul, #wemd ol",
    "list_item_row": "#wemd li",
    "list_item_text": "#wemd li",
    "footnote_section": "#wemd section.citations",
    "footnote_title": "#wemd .citations h2",
    "footnote_item": "#wemd .citations li",
    "footnote_sup": "#wemd .citations a",
    "callout": "#wemd section.callout",
    "callout_title": "#wemd .callout-title",
    "callout_content": "#wemd .callout p:not(.callout-title)",
}

# 本仓渲染器不做的事：代码头栏、自定义列表符号行（::marker 无法内联）
SKIPPED_KEYS = {"code_header", "code_block", "list_item_bullet", "ol_item_bullet"}
SKIP_PROPERTIES = {"content"}


def snake_to_css(prop: str) -> str:
    return prop.replace("_", "-")


def convert_theme(json_path: Path) -> tuple[str, dict]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    rules: list[tuple[str, list[str]]] = []
    seen_selectors: set[str] = set()
    for key, selector in SELECTOR_MAP.items():
        block = (data.get("styles") or {}).get(key)
        if not isinstance(block, dict):
            continue
        declarations: list[str] = []
        for prop, value in block.items():
            if prop in SKIP_PROPERTIES or not isinstance(value, (str, int, float)):
                continue
            value = str(value).strip()
            if not value:
                continue
            declarations.append(f"{snake_to_css(prop)}:{value}")
        if selector == "#wemd":
            # 本仓根样式契约（body-first）：根节点只保留排版属性，
            # 背景/卡片壳（padding、阴影、圆角、限宽）一律剥掉——
            # 微信正文容器固定，主题个性靠排版与元素样式表达。
            declarations = [
                d
                for d in declarations
                if not re.match(r"^(background|padding|margin|max-width|box-shadow|border-radius|border)\b", d)
            ]
            declarations.append("background:transparent")
            if not any(d.startswith("font-size:") for d in declarations):
                # 根样式契约要求 root 携带 font-size（body-first 排版基准）
                declarations.append("font-size:15px")
        if selector in seen_selectors:
            # 同选择器出现两次（list_item_row/text）时合并声明
            for idx, (sel, decls) in enumerate(rules):
                if sel == selector:
                    rules[idx] = (sel, decls + declarations)
                    break
            continue
        seen_selectors.add(selector)
        if declarations:
            rules.append((selector, declarations))
    if "#wemd" not in seen_selectors:
        # 个别主题无 wrapper 键：根规则兜底，保证 body-first 契约成立
        rules.insert(0, ("#wemd", ["background:transparent", "font-size:15px", "line-height:1.75"]))
    return build_css(rules), data


def build_css(rules: list[tuple[str, list[str]]]) -> str:
    chunks = []
    for selector, declarations in rules:
        if not declarations:
            continue
        chunks.append(f"{selector} {{\n  " + ";\n  ".join(declarations) + ";\n}")
    css = "\n\n".join(chunks)
    # 微信安全换行：pre/pre code 强制 pre-wrap；去掉 min-width:max-content
    for sel in ("#wemd pre", "#wemd pre code"):
        css = re.sub(
            rf"({re.escape(sel)} \{{[^}}]*?)white-space:[^;]+;",
            r"\1white-space: pre-wrap;",
            css,
        )
        css = re.sub(
            rf"({re.escape(sel)} \{{[^}}]*?)word-break:[^;]+;",
            r"\1word-break: break-all;",
            css,
        )
        css = re.sub(rf"{re.escape(sel)} \{{([^}}]*?)min-width:[^;]+;\n", rf"{sel} {{\1", css)
        if f"{sel} {{" in css:
            # 保证两个属性都存在
            def ensure(match: re.Match) -> str:
                body = match.group(1)
                if "white-space" not in body:
                    body += "\n  white-space: pre-wrap;"
                if "word-break" not in body:
                    body += "\n  word-break: break-all;"
                return f"{sel} {{{body}\n}}"

            css = re.sub(rf"{re.escape(sel)} \{{([^}}]*)\n\}}", ensure, css, count=1)
    # 图片不撑破容器
    if "#wemd img {" in css and "max-width" not in css.split("#wemd img {")[1].split("}")[0]:
        css = css.replace("#wemd img {", "#wemd img {\n  max-width: 100%;", 1)
    return css + "\n"


def slugify(name: str) -> str:
    # 主题名校验要求 ^[a-z][a-z0-9_]{1,63}$：只能用下划线
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "theme"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    source_dir = Path(sys.argv[1])
    json_files = sorted(source_dir.glob("*.json"))
    if not json_files:
        print(f"no theme json found in {source_dir}")
        return 2

    from qiao_wechat.services.renderer import WeChatMarkdownRenderer, refresh_theme_cache
    from qiao_wechat.services.theme_manager import ThemeManager

    sample_path = REPO_ROOT / "examples" / "regression" / "sample_full.md"
    sample_md = sample_path.read_text(encoding="utf-8") if sample_path.is_file() else "# 标题\n\n段落 `code`。\n"

    existing = set(WeChatMarkdownRenderer.available_themes())
    manager = ThemeManager()
    imported: list[str] = []
    skipped_existing: list[str] = []
    failed: list[tuple[str, str]] = []

    for json_path in json_files:
        stem = json_path.stem
        target_name = f"xh_{slugify(stem)}"
        if target_name in existing:
            skipped_existing.append(target_name)
            continue
        try:
            css, data = convert_theme(json_path)
            # 结构验证：用该主题渲染样本，必须产出非空内联样式且关键元素在位
            rendered = WeChatMarkdownRenderer(theme_name=target_name).render(sample_md, title="主题验证") if False else None
            manager.import_css(
                name=target_name,
                css=css,
                display_name=data.get("name") or stem,
                description=(data.get("description") or "")[:500],
                source="xiaohu-wechat-format",
                source_url=SOURCE_REPO,
                tags=["xiaohu", "imported"],
                overwrite=False,
            )
            refresh_theme_cache()
            rendered = WeChatMarkdownRenderer(theme_name=target_name).render(sample_md, title="主题验证")
            style_count = rendered.count('style="')
            if style_count < 10 or "<table" not in rendered or "<pre" not in rendered:
                raise ValueError(f"rendered output too sparse (styles={style_count})")
            if "font-size:" not in rendered or "margin-bottom:" not in rendered:
                raise ValueError("multi-word CSS properties missing - property conversion bug")
            imported.append(target_name)
        except Exception as exc:  # noqa: BLE001 - 单主题失败不阻断批量
            failed.append((target_name, str(exc)[:160]))
            # 回滚半导入（import_css 成功但渲染验证失败时）
            try:
                theme_file = manager._css_path(target_name)  # noqa: SLF001 - 同仓维护脚本
                if theme_file.exists():
                    theme_file.unlink()
                    refresh_theme_cache()
            except Exception:  # noqa: BLE001
                pass

    print(f"来源：{len(json_files)} 个主题 JSON")
    print(f"导入成功：{len(imported)} 套 → {', '.join(imported[:8])}{' ...' if len(imported) > 8 else ''}")
    if skipped_existing:
        print(f"已存在跳过：{len(skipped_existing)}")
    if failed:
        print(f"导入失败（已回滚）：{len(failed)}")
        for name, err in failed:
            print(f"  - {name}: {err}")
    print(f"当前主题总数：{len(WeChatMarkdownRenderer.available_themes())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
