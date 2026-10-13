# qiao-wechat 启动协议

本仓采用 SpecMesh v1.1。先读本文件与 [PROJECT.md](PROJECT.md)，再按任务选择文档。

## 渐进读取

- 排版、素材、入口或状态变更：读 [架构](docs/ARCHITECTURE.md) 相关段落。
- 讨论重写、合仓、队列、账号权限：先读 [决策](docs/DECISIONS.md) 和 [OPS.md](OPS.md)。
- 当前收敛任务：读 [计划](plans/2026-09-draft-workflow-reliability/task_plan.md) 与 [进展](plans/2026-09-draft-workflow-reliability/progress.md)，按需读 [findings](plans/2026-09-draft-workflow-reliability/findings.md) 和相关代码。
- 仅当任务涉及某个 skill 时读 `skills/` 下该 skill，不默认加载整个目录。
- 已有命令、端口、主题路径、CLI/API 示例的原始说明保存在 [docs/USAGE.md](docs/USAGE.md)，按需查阅，不在 README 重复。

## 项目规则

- 木乔公众号2026-10-14新策略：内容和微信草稿由用户验收；不调用独立内容模型评审，不做投递后自动回读。仅保存API成功响应，明确待用户验收；未知提交仍不盲重发。
- 本用户已定稿Markdown先走内容仓`automation/daily-wechat/package_article.py`的固定math-analysis-v1模板与独立配图入口，再以HTML进入本仓；不自行改用wemd_clean等通用主题。436内容仓为`/srv/self-media-exchange/inbox/xueyu-gongzhonghao-receive`；规则见该仓`docs/confirmed-article-layout.md`。通用库和用户另行指定的外部HTML入口保留。

- 外部 HTML 入口保留为一等入口，不隐式套 Markdown 主题或另造投递实现。
- 创建草稿不等于发布；默认不开手机预览/正式发布，能力以对应账号证据为准（详见 OPS.md）。
- 保留 WCM_ 配置兼容、CLI 行为及内容仓路径；迁移先准备兼容和回退。
- 本地验证使用临时 DB、输出、上传及所有 outbox 路径，不触碰生产数据。
- 真实账号外发动作按当前会话授权执行；先完成本地可评审部分，不反复询问已授权动作。
- 不把 README/旧 progress 中的历史"通过"当成本次测试证据。

## 连续性维护

- 意图/约束/最高优先级改变才更新 PROJECT。
- 架构改变更新 ARCHITECTURE；重要取舍更新 DECISIONS，注明建议或已采用。
- 任务发现先记 `findings.md`；`progress.md` 只保留 Current/Done/Remaining/Issues/Next。
- 根目录旧的 `wx-content-mesh-python → qiao-wechat` rename 计划已归档到 `plans/2026-09-rename-archive/`（待与本次任务一起迁移）；不要未经核对把未完成项勾完。
- 每轮变更走单一合入路径；发布前核对开放 PR 是否吸收、重写、rebase 或放弃，以代码/测试/补丁证据判断。

## 验证

按计划相关矩阵运行现有测试；新测试只覆盖行为缺口。安装验证必须包含 wheel 安装后的 CLI 与资源读取。
远端草稿验收单列，不用 mock 测试代替；只有对应成功标准满足才标任务完成。
