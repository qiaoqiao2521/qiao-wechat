# qiao-wechat — Project Context

- SpecMesh: 1.1.0
- 文档基线：2026-09-07，commit `545f592200cd7cf0583fb7ad3874abf8e17d0000`（main 快照）；2026-09-14 完成第一轮本地实施（未提交/未推送）
- 软件计划：见 [plans/2026-09-draft-workflow-reliability/](plans/2026-09-draft-workflow-reliability/)，**阶段 0–4 本地部分完成；阶段 5 仅剩真实账号远端验收（需当次授权，预检脚本已就绪）**。

## Why

让已经完成创作的文章，经过可检查的排版和素材处理，可靠进入微信公众号草稿箱。
减少在 Markdown、外部 HTML、图片路径、微信素材与草稿之间的重复搬运和排错。

## User Intent

- 2026-10-14木乔公众号采用用户亲验：取消内容模型评审及提交后自动微信回读。已定稿复用内容仓固定数学模板和独立配图，再从HTML入口投递；成功响应标已提交/待用户验收，保留未知提交保护。通用Markdown/HTML能力不重写，使用方不得把通用主题当成本用户固定模板。

- 保留 Python 工具链，用 CLI 或轻量 Web/API 完成文章交付。
- 同时接收 Markdown 与外部已经排好的 HTML；外部 HTML 不重新走 Markdown 主题渲染。
- 用户能看本地预览，确认正文和封面，再检查公众号后台草稿。
- 内容创作及个人 IP 文风流程由内容仓/上游 workflow 负责，本仓提供最后的排版与投递能力。
- 排版层方向以 WeMD / xiaohu-wechat-format 为准（微信兼容 inline HTML、主题 CSS 即数据）；wxAiPost 类项目只作发布链路结构参考，不是最终方案（2026-09-14 确认，见 DECISIONS D15）。
- 小红书保持导出包路径。
- 上述意图来自现有 README 与代码边界；本次未另行确认商业化、多用户或自动发布目标。

## Non-goals

- 本轮不建设选题平台、通用 Agent 工作室、素材搜索平台或新的完整 CMS。
- 不重写 Go/Vue/MySQL 栈，不以换框架、加队列或加前端作为完成指标。
- 不删除现有科研 skills、creative pipeline、XHS 导出；先保持兼容，不扩展这些支线。
- 不把微信草稿创建、本地预览、手机预览与正式发布混为同一件事。
- 不依据历史个人号记录推导所有账号的现行权限。

## Success（建议本轮验收目标）

- 干净安装后，Markdown 与 HTML 两条入口都可完成本地预览及模拟草稿闭环。
- 外部 HTML 保留内容次序与样式意图，变化只来自已记录的微信兼容处理。
- 图片、封面、列表、表格、图形和公式的支持/不支持边界明确，错误可定位。
- 重复提交和结果不确定时，用户知道是否已创建草稿及下一步；不承诺远端 exactly-once。
- 失败记录可在新数据库会话查询，且失败重试不会错误标记为已发布。
- 真实账号在另行授权和环境就绪后，完成至少一篇 Markdown 和一篇 HTML 草稿验收。
- 新接手者按索引可以找出当前阶段、证据和下一步，不需要重讲背景。

## Constraints

- Python ≥ 3.11；当前包版本 0.3.0，FastAPI、SQLAlchemy、SQLite、requests、CSS 内联渲染。
- 包名/导入/CLI 保留 `qiao-wechat` / `qiao_wechat`；WCM_ 环境变量有既有兼容意义，不能机械改名。
- 默认主题 `wechat_baseline`；默认 `draft_only`，手机预览与发布开关关闭。
- `OPS.md` 是 2026-04-28 账号实测记录，不是现行平台能力保证。
- 现有 source/outbox 路径与内容仓有同步约定；变更路径、DB 或状态需兼容与迁移方案。
- 当前服务按受信任本地操作者边界评估；本轮不宣称已有互联网多租户访问控制。
- 文档交付不构成上传文章、创建真实草稿、发送预览或公开发布的授权。

## Current State

已在基线源码确认：

- CLI、FastAPI（24 条路由）、HTML 投递工具页（`/tools/html-draft`）、主题画廊（`/preview/themes`）和主题管理入口存在。
- `PublishService` 统一文章渲染、素材上传、草稿和可选发布流程；HTML 入口设置 `content_ingress=html` 后复用同一服务。
- HTML 字符串、HTML 文件、Markdown 三条入口分别走 `prepare_html_article` 与 `create_wechat_draft`，最终汇总到同一发布链路（无分叉实现）。
- 素材按 `account_id + sha256 + purpose` 建立唯一约束。
- 文章/任务/账号/素材表已存在；调度器仍为进程内 `BackgroundScheduler`。
- `image_service.py` 内置 SVG→ImageMagick `convert`、raster 多档压缩、20MB 上限等"准备"逻辑，但 README 的"图形已支持"需按类型核对，不能统一承诺。
- 原根目录 `task_plan.md / progress.md / findings.md` 是 `wx-content-mesh-python → qiao-wechat` rename 计划收尾；当前代码是否已完成须用安装/回归验证判定（见 [plans/2026-09-draft-workflow-reliability/findings.md](plans/2026-09-draft-workflow-reliability/findings.md) F01）。

未完成或待证明：

- 真实账号远端验收（两篇草稿 + 后台人工检查）未执行；本轮只做了本地预览、导出与模拟草稿接口。
- 阶段 4 操作反馈（工具页/CLI 失败与重复提交展示）未实施；后端契约（error_category/draft_outcome/force_new）已就绪。
- 多进程部署下的幂等窗口与生产旧库升级演练未验证（本地 legacy 库迁移已有测试）。
- 2026-09-14 实施已在本地代码与测试就位（pytest 65 项、回归 runner 19 项、干净安装 wheel 验证），变更尚未提交到 git。

## Current Priority

建议顺序：

- ~~P0 干净安装与双入口基线 → P0 失败持久化/重复提交 → P1 排版素材契约~~（已完成）
- P1 操作反馈（阶段 4）→ P1 真实账号发布验收（阶段 5，需另行授权）。

在真实瓶颈出现前不替换 APScheduler 或引入 Redis/Celery。

## Knowledge Map

- 2026-09-27 用户选定「蓝金科技专题」v1.0，登记为可选主题 `blue_gold_editorial`。配色禁用紫色，组件按文章内容选用；说明与不同内容的复用示例见 [docs/themes/blue-gold-editorial/README.md](docs/themes/blue-gold-editorial/README.md)。保留现有默认主题；该登记不代表微信手机真机验收。

- 启动协议：[AGENTS.md](AGENTS.md)
- 用户入口：[README.md](README.md)
- 当前架构：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 决策与重访条件：[docs/DECISIONS.md](docs/DECISIONS.md)
- 操作说明（保留原 README 工作流）：[docs/USAGE.md](docs/USAGE.md)
- 历史账号约束：[OPS.md](OPS.md)
- 当前计划：[plans/2026-09-draft-workflow-reliability/task_plan.md](plans/2026-09-draft-workflow-reliability/task_plan.md)
- 当前证据：[plans/2026-09-draft-workflow-reliability/findings.md](plans/2026-09-draft-workflow-reliability/findings.md)
- 当前进展：[plans/2026-09-draft-workflow-reliability/progress.md](plans/2026-09-draft-workflow-reliability/progress.md)
- 旧 rename 计划处置：[plans/2026-09-rename-archive/](plans/2026-09-rename-archive/)（仅迁移/指向，不冒充新工作）
