# 当前架构

基线：`545f592200cd7cf0583fb7ad3874abf8e17d0000`，2026-09-07。本文描述已读源码；未来改进见 [计划](../plans/2026-09-draft-workflow-reliability/task_plan.md)。

## 总览与数据流

```text
内容仓 / 上游创作系统
    ├─ Markdown → CLI/API → Article → renderer + theme CSS
    └─ 已渲染HTML/HTML文件 → normalize + prepare_html_article
                  ↓
          本地预览与正文HTML
                  ↓
       PublishService.create_wechat_draft
       图片解析/上传、封面准备、正文兼容处理与校验
                  ↓
           WeChatApiClient → draft/add
                  ↓
       Article草稿标识 + PublishJob记录
                  ↓
         公众号后台人工检查/发布

可选分支：账号允许且开关启用 → 手机预览 / freepublish / poll
独立分支：XhsExporter → 导出材料
```

## 组件地图

| 领域 | 当前入口 | 责任 |
|---|---|---|
| CLI与API | `qiao_wechat/cli.py`、`app.py`、`schemas.py` | 命令/请求解析、轻量页面、文章/任务查询；FastAPI 共 24 条 `@app.*` 路由 |
| 配置与数据 | `config.py`、`db.py`、`models.py` | WCM_ 配置、会话事务、SQLAlchemy 模型 |
| 投递编排 | `services/publisher.py` | 共用 Markdown/HTML 投递、封面、校验、草稿和发布状态；HTML 入口设置 `content_ingress=html` 后复用同一服务 |
| 排版 | `services/renderer.py`、`html_normalizer.py`、`themes/` | Markdown 主题内联、HTML 兼容整理；CSS 编译 → inline HTML |
| 图形与素材 | `visual_renderer.py`、`image_service.py`、`obsidian_assets.py` | 图形 URL、SVG/raster 准备与多档压缩、引用解析、素材上传/复用；SVG 依赖系统 `convert` |
| 主题展示 | `theme_manager.py`、`theme_gallery.py` | 导入 CSS、元数据、画廊页面（`/preview/themes`）与管理 API |
| 内容仓桥接 | `wechat_repo_flow.py` | 草稿/待发布目录、质量检查、入草稿箱、显式归档 |
| 其他现有能力 | `creative_pipeline.py`、`quality_gate.py`、`llm.py`、`xhs_exporter.py` | 可选创作、质量提示、模型调用、XHS 导出 |
| 调度 | `scheduler.py` | 内存定时提交与轮询，不是可恢复持久任务队列 |

服务文件位于 `qiao_wechat/services/`。用户详细命令见 [USAGE](USAGE.md)。

## 数据与所有权

- 内容仓拥有文章原始文稿、个人文风与素材；本仓保存投递过程中需要的文章记录和产物。
- `Article` 有正文、主题、状态、本地预览路径、草稿 media_id、发布标识及 meta。`meta` 携带 `content_revision`（内容修订哈希）与 `draft_outcome`（created/reused/failed/unknown）。
- `PublishJob` 是一次渠道操作记录，状态 `pending/running/success/failed`；wx_draft 任务额外有 `request_key`（幂等键）与 `error_category`（失败分类），仍不等于可恢复作业队列。
- `MediaAsset` 唯一键为 `account_id + sha256 + purpose`，解决素材级复用；文章级幂等由 `request_key` 去重承担。
- `db_session` 和 API `get_db` 在成功时 commit、异常时 rollback。草稿投递路径在服务内即时 commit 远端结果（失败记录同理）；commit 本身失败时用独立会话兜底留存 unknown/failed 记录（见 `publisher._persist_failure`），外层回滚不再抹掉尝试记录。
- 数据库启动执行 `create_all`、`_ensure_schema_columns`（旧库 ADD COLUMN 迁移）及枚举修复；新增枚举值仍须设计迁移，普通新列可加入 `_SCHEMA_COLUMNS`。
- `preview/upload/outbox` 为运行产物；不要提交账号凭据、真实文章正文或真实接口响应到公开验收材料。
- 本地预览会把 Markdown/HTML 引用的本地相对图片复制到 `outputs/article_N/assets/` 并改写预览 src；`article.html` 保持原样，等上传阶段处理。

## 关键不变量

- HTML 入口设置 `content_ingress=html` 并经 `prepare_html_article`；进入共用草稿服务时避免 Markdown 重渲染；`<head><style>` 规则按选择器内联到元素 style，`<style>` 标签移除。
- 微信导出由 `wechat_export.py` 和 `HtmlNormalizer` 处理：保护正文与代码空白，站外来源降级为去重的可见完整URL，合法微信链接保持href；只在中性DOM继承可证明等价时删除重复样式。最终HTML按20000字符/1MiB排他上限分项计量，超限拒绝，不截正文。
- `limits.py` 统一标题32字符、摘要120字符与双裁剪字段；提供裁剪时要求2.35_1和1_1两套归一化窗口，并绑定草稿请求身份。批准后的标题、作者或摘要变化必须重新prepare/render；旧unknown请求仍阻断未核对重发。
- 原始HTML/Markdown渲染结果在媒体替换前记录语义指纹；源稿更新通过prepare/render刷新指纹。正文导出和实际微信回读分别验证，后者不能替代前者。
- 草稿幂等：同账号 + 同 `request_key` 的成功结果直接复用（不重复调用 `draft/add`）；结果未知的尝试默认拦截重发，只有显式 `force_new`（CLI `--force-new` / API 字段）放行。
- `content_revision` 在图片/素材 URL 替换之前计算（Markdown 源文或 normalize 后的 HTML + 主题 + 元数据），保证重试间稳定。
- 远端调用成功后的状态提交在服务内完成；提交失败 → 独立会话写入 `remote_result_unknown` 记录，供人工核对草稿箱后决定重试。
- `draft_created` 与 `published` 不同；`archive_pending_after_publish` 是独立文件移动动作，不能由草稿成功自动触发。
- `preview`/`publish` 开关默认关闭，不能因上游请求了某个功能就自动打开。
- 素材缓存隔离账号与用途；同一图片不能跨账号复用微信 `media_id`。
- 远端成功与本地事务提交不是原子操作；连接超时不等于远端必然失败。
- 受信任本地文件入口与 URL 下载存在真实文件/网络边界，部署到更宽网络范围前需另行设计鉴权及访问策略。

## 外部依赖与脆弱区域

- 微信 token、素材、draft、freepublish 依赖账号权限和平台行为。OPS 仅保存历史证据。
- 图形 URL 使用配置的 Kroki / CodeCogs；SVG 上传依赖 ImageMagick `convert`。网络失败、工具缺失与输出错误须区分。
- Mermaid 目前不渲染（源码保留在代码块，属已验证边界）；PlantUML/Graphviz/公式经 Kroki/CodeCogs 转图。主题代码块统一 `pre-wrap`，防止微信端长代码行被裁剪。
- `pyproject.toml` 现包含 `qiao_wechat.services` 子包与 `themes/*.css|json` 资源；安装制品已验证（见任务进展）。
- 默认路径包含部署专用 `/srv/...` 目录；目录创建已移出 import 路径，由 `init_db()`（应用/CLI 入口）触发。
- 进程内 per-request-key 锁只抑制单进程并发；多进程部署下幂等依赖 `request_key` 持久记录，提交窗口仍非跨进程原子。
- HTML 兼容整理（含 head 样式内联）不等同于通用不可信 HTML 净化。需要公开输入服务时单独定义范围。

## Read Next

- 事务和重复请求：[当前计划](../plans/2026-09-draft-workflow-reliability/task_plan.md) 阶段 2。
- 排版和图片：现有 `tests/test_renderer.py`、`test_html_normalizer.py`、`test_image_service.py`、`test_publisher.py`。
- API/CLI 入口：`tests/test_html_draft_api.py`、`test_html_draft_file_api.py`、`test_cli_html_draft.py`。
- 产品取舍：[DECISIONS](DECISIONS.md)；历史权限：[OPS](../OPS.md)。
