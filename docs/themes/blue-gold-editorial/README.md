# 蓝金科技专题 v1.0

固定主题名：`blue_gold_editorial`。保留深蓝卷首、清晰大字章节、蓝青信息层级、金色强调及白底正文。禁用紫色、紫粉和薰衣草色，包括插图、代码高亮与浅色底纹。

本仓已登记此可选主题，CSS 唯一源位于 [blue_gold_editorial.css](../../../qiao_wechat/themes/blue_gold_editorial.css)。对已有 Markdown 文章可使用 `qiao-wechat render <article_id> --theme blue_gold_editorial`。本次没有改变全局默认主题；以下导入示例供独立模板包使用。

## 使用方式

普通文章直接使用 Markdown。需要专题效果时，从 `component-example.md` 复制语义 HTML 组件，替换内容后仍交给现有 `WeChatMarkdownRenderer`。可用组件：卷首、编号章节、双色对照、步骤轴、指标、代码窗口、洞察、警示、大字引用、总结和署名。全部可选；不限定章节、步骤、指标或总结条目的数量。

复用约束：

- 本主题只负责格式。保留原稿正文、标点、顺序、数值与引用，不为凑组件改写内容。
- 正文 16px / 1.95 行高；主章节约 26px。强调在蓝、青、金中选取，避免给每一段套卡片。
- 指标容器 `metrics-items` 默认保持三列展台效果，适合 2–3 个短值。超过三项或值很长时加 `metrics-stack`，改为纵排；不强行压入一行。
- 编号与标签是 HTML 中的真实文本，不依赖 CSS 伪元素。它们是可编辑内容，主题不会自动改编号。
- 尽量使用 `section`、`p` 和普通文流；横排指标使用 `display:table`。不依赖 flex、grid、绝对定位或脚本。
- 深色区块使用明确文字颜色。渐变同时有纯色背景作回退；装饰被客户端移除时仍可阅读。
- 卷首图是可选、文章专属资产，不把本篇插图固化成通用主题。新图也要遵循无紫色约束。
- HTML 组件里的文本按 HTML 转义；代码中的 `<`、`>`、`&` 分别用 `&lt;`、`&gt;`、`&amp;`。需要在容器内写 Markdown 时使用现有 `markdown="1"` 机制。

## 主题资源位置

主题已登记在包内，无需再次导入。元数据位于 `qiao_wechat/themes/metadata.json` 的 `blue_gold_editorial` 条目；CSS 位于同目录的 `blue_gold_editorial.css`。示例 Markdown 保存在本目录，生成的 HTML 和预览写入临时输出目录，不作为第二套主题源。

## 用现有 renderer 渲染

在本仓 Python 环境即可运行：

```python
from pathlib import Path
from qiao_wechat.services.renderer import WeChatMarkdownRenderer

source = Path("article.md")
renderer = WeChatMarkdownRenderer(theme_name="blue_gold_editorial")
html = renderer.render(source.read_text(encoding="utf-8"))
Path("article.html").write_text(html, encoding="utf-8")
renderer.save_preview(html, Path("preview.html"), page_title="文章预览")
```

渲染产物已内联样式，沿用现有 HTML 投递入口即可。已经排好的外部 HTML 不会因为选择主题名而自动重新套版，需要显式使用此组件与渲染流程。

## 验证范围

`component-example.md` 是两节“家庭网络排障”示例，覆盖所有扩展组件；`plain-example.md` 是“阳台植物观察”普通 Markdown，覆盖标题、列表、引用、代码、表格和原生 callout。示例文字及数值均为演示，不作为文章生产事实。

使用上述脚本可从两个示例生成 HTML 正文与预览。浏览器预览和 CSS 内联检查不等于手机微信验收；发布前继续沿用现有的远端回读和手机检查流程。
