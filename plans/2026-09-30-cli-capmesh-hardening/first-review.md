# 独立审批

审批时间：2026-09-29T17:47:59.579252+00:00。本次结论为 **AGY ACCEPT / ZCode REJECT / CBC ACCEPT**；交接完成不等于全部子项接受。

| 子项 | 裁决 | 独立证据与责任断言 |
|---|---|---|
| AGY | ACCEPT | 正常14通过；变体exit 1，在 `test_capmesh_agy.py:169` 报 DID NOT RAISE ValueError。关闭 session/engine 后重新打开文件 SQLite，unknown/failed 记录可查询；no-op 屏障被重试触达。正常重试调用总数1，force_new成功后2。 |
| ZCode | REJECT | CLI运行600秒超时，exit 143；新测试、三份handoff文档和正常/变体日志均缺失。独立verify返回missingTest，没有执行pytest；包装器exit 0不算通过。 |
| CBC | ACCEPT | 正常14通过；变体exit 1，在 `test_capmesh_cbc.py:167` 检出真实cmd_draft stderr缺失“草稿箱/核对”。仅替换真实错误分类函数的unknown分支，首次Timeout保持原行为，新session默认重试触达持久化unknown的ValueError；正常两次CLI调用均exit 1，远端总调用1。 |

正确性：AGY证明持久化unknown的默认屏障与显式force_new，CBC证明后续CLI重试的退出码和行动指引。数据完整性：AGY证明外层rollback及连接重建后Article/PublishJob留存；CBC证明新session可查unknown、failed及request_key。ZCode的同账号重开session成功复用、跨账号隔离新边界未覆盖。隐私/权限、性能、兼容性、可访问性均未覆盖。

变体失败均来自预期业务断言，无导入错误或测试超时。AGY变体未执行失败点之后的计数断言；CBC变体未执行指引失败点之后的断言，不能把这些后续断言另计为检出。正常模式执行了全部断言。AGY/CBC模型进程均exit 0、未超时；ZCode失败如上。

对三个工作副本逐文件核对145个既有源码/测试/文档文件，无修改或删除；仅AGY/CBC分别新增本项测试。experiment中8个基线文件哈希在baseline及三个副本均匹配。原始执行日志保存在 `/home/muqiao/Documents/Codex/2026-09-27/t/work/paperclip-wechat-20260930/approval-evidence` 的 `*.initial.*.log`；独立复跑日志在 `/home/muqiao/Documents/Codex/2026-09-27/t/work/paperclip-wechat-20260930/{agy,cbc}/handoff/{normal,mutant}.log`，三项verify结果及哈希在 `/home/muqiao/Documents/Codex/2026-09-27/t/work/paperclip-wechat-20260930/approval-evidence/verification.json`。

仅将接受的两个测试复制到baseline，先确认baseline和主仓无同名文件。使用 `worker.test_env({'cwd': baseline})`、`/home/muqiao/Documents/Codex/2026-09-27/t/work/paperclip-wechat-20260930/venv/bin/python`，在baseline统一运行 `test_draft_reliability.py`、`test_operation_feedback.py` 与两个新测试：**15 passed，exit 0，无超时**；日志 `/home/muqiao/Documents/Codex/2026-09-27/t/work/paperclip-wechat-20260930/approval-evidence/combined.log`。现有fixture禁止真实网络，隔离副本无.env；未读取生产.env。合并通过后才复制到主仓，三处新测试SHA-256一致，主仓151个受保护既有文件哈希不变，未提交或推送。只有1条既有Starlette/httpx弃用警告。

ZCode若另行重开：由原CLI交付两账号同内容、同账号关session重载后的远端调用计数，注入仅禁用`_try_reuse_previous_draft`的变体，并补齐handoff及两份执行日志后重新审批。本轮不使用可选修正额度，不代写测试，拒绝结论保留。

原生父任务运行记录 `/home/muqiao/Documents/Codex/2026-09-27/t/work/paperclip-wechat-20260930/approval-evidence/coordinator-runs.json`：派发run于2026-09-29 17:33:15.583Z结束，下一审批run于17:43:05.581Z开始，中间没有该父任务协调者run。仅据此确认事件间未保持协调者run，不推断平台总费用为零。真实微信、跨进程并发、真实进程重启与远端exactly-once未验收。
