# qiao-wechat

Python 公众号排版与草稿交付工具。接收 Markdown 或已渲染 HTML，处理正文图片和封面，生成本地预览并提交微信草稿。

## 能做什么

- Markdown 主题 CSS 内联、主题管理及预览。
- HTML 字符串/文件直入共用草稿链路（不重渲染 Markdown 主题）。
- 文章、素材与发布任务记录；可选的小红书导出。

默认采用「本地预览 → 微信草稿箱 → 后台人工检查/发布」。手机预览和自动发布默认关闭；历史账号能力见 [OPS.md](OPS.md)，执行前按目标账号核验。

## 安装与运行

需要 Python 3.11+。以下为源码开发路径；发行制品（wheel）验证属于 [当前计划](plans/2026-09-draft-workflow-reliability/task_plan.md) 阶段 1。

```bash
uv sync --extra dev
cp config.example.env .env
# 编辑 .env，至少把 WCM_DATABASE_URL / 输出目录 / 内容仓目录指向本机可用路径
uv run qiao-wechat init
uv run uvicorn qiao_wechat.app:app --host 127.0.0.1 --port 8000
```

打开本机 `http://127.0.0.1:8000/docs` 或 `http://127.0.0.1:8000/tools/html-draft`。
主题画廊/主题管理接口位于同源 `/preview/themes` 与 `/themes/*`（与 `/docs`、`/tools/*` 同进程，不另起端口）。

> 历史 README 中出现过 `127.0.0.1:8001/preview/themes` 与 `127.0.0.1:8001/themes/*`，那是早期主题服务独立进程的写法。当前 FastAPI 入口（`qiao_wechat.app:app`）已把主题画廊与管理 API 合并到主进程；请以实际启动命令的端口为准。

> SVG 图片路径仍依赖 ImageMagick 的 `convert`；图形与公式还需要外部渲染服务（Kroki / CodeCogs）。详细支持边界见 [docs/USAGE.md#排版引擎](docs/USAGE.md) 与 [ARCHITECTURE](docs/ARCHITECTURE.md)。

## 从哪里继续

- 项目目的、边界与优先级：[PROJECT.md](PROJECT.md)
- 维护者入口与启动协议：[AGENTS.md](AGENTS.md)
- 架构与重要取舍：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)、[docs/DECISIONS.md](docs/DECISIONS.md)
- 操作命令、CLI/API 示例、主题画廊、HTML 投递示例：[docs/USAGE.md](docs/USAGE.md)
- 当前工作：[plans/2026-09-draft-workflow-reliability/task_plan.md](plans/2026-09-draft-workflow-reliability/task_plan.md)、[plans/2026-09-draft-workflow-reliability/progress.md](plans/2026-09-draft-workflow-reliability/progress.md)
- 历史账号约束：[OPS.md](OPS.md)

本次 SpecMesh 整理基于 `545f592200cd7cf0583fb7ad3874abf8e17d0000`，2026-09-07；新增计划尚未实施。

## 本仓库曾用名

仓库早期公开名为 `wx-content-mesh-python`（包名 `wx_content_mesh`）。重命名后已统一为 `qiao-wechat` / `qiao_wechat`，旧根目录 rename 计划的处置见 [plans/2026-09-rename-archive/](plans/2026-09-rename-archive/)。当前 README 不再使用旧名作为项目边界或链接路径。
