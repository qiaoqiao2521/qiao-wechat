# CLI 派发、休眠、审批实测

日期：2026-09-30，北京时间。本机 Paperclip 2026.916.1，公众号仓库 qiao-wechat。

## 结论
纯 CLI 的派发→结束 Codex 推理→三个结果交齐→原生事件唤醒→独立审批已实测成立；不是三份自评直接当通过。
最终 AGY/CBC 接受，ZCode 拒绝。接受的两个测试已放回主仓，合并15项通过；第三项未合入。没有发现本轮范围内必须修改的产品源码缺陷。

## 关键执行证据
- 主任务 QIA-2 / 4dc832a5-adf8-4cd5-b843-656a4195bdcd。
- 协调 Codex 共 3 个运行：首次派发、首次审批、ZCode纠偏后的最终审批。无定时heartbeat，未调用模型轮询。
- 第一次派发运行在 2026-09-29T17:33:15.583Z 结束，审批在 2026-09-29T17:43:05.581Z 启动，中间 589.998 秒无协调Codex运行（约9分50秒）。AGY/CBC先完成，没有提前唤醒。
- 第二轮只重开ZCode原子任务，续接 原私有会话（未入库）；AGY/CBC没有再调用模型。ZCode95.5秒返回，随后完成事件启动最终审批。
- 原生运行证据见 orchestration-runs.json；任务最终状态见 task-statuses.json。所有本次运行已终止。这些运行态JSON保留在本机私有目录，不随Git分发。

## 真正交付
- tests/test_capmesh_agy.py：文件SQLite关闭连接后重开，unknown仍可查询，默认阻止重发，显式force_new才放行；对应屏障变体被业务断言检出。
- tests/test_capmesh_cbc.py：持久化unknown后的CLI重试给出草稿箱核对指引，非零退出且不误发第二篇；错误消息变体被真实stderr断言检出。
- ZCode：首轮600秒只读/搜索未落文件，超时仍交回失败并触发审批。唯一纠偏解决缺产物，但正常测试因关闭session后读取失效对象ID报DetachedInstanceError；第二账号路径未到达。虽成功抓到重复提交变体，仍拒绝。

## Paperclip 能替代到哪一步
本轮原生派发、子任务依赖、完成事件、Codex会话延续能用。AGY/ZCode/CBC通过原生process adapter加任务专用worker.py启动，bridge.py只负责一轮API读写与SpecMesh路径，不是新调度器。不能声称外部CLI已开箱即用接入。

审批能力来自Codex及明确的验收契约，Paperclip只保证工作流。子任务done在本实验专指真实结果包交接完成；模型失败/超时和产品验收状态分开保存。包装器exit0、任务done都不意味着产品PASS。

没有测试服务器断电、wrapper硬崩溃、丢失回报、多机、真正人工审批暂停等恢复路径。原生child blocked/cancelled还有提前异常唤醒语义；本轮失败通过完整结果包上报，不等于所有异常都聚合后才醒。

## 等待与成本
普通只读observe.py每5秒采集运行状态用于验收，没有发模型请求。宿主对话使用阻塞工具等待；协调Codex是真正结束进程。记录能证明协调者空闲区间无run，不能宣称整次测试总token或费用为零。process adapter未自动汇总外部CLI token，不能给三者成本排名。

## 边界与复用
所有定时唤醒关闭；保留本次4个测试Agent和任务供复核，没有增加cron。ZCode打回原因见review.md；一次纠偏预算用尽，不继续盲重试。纯本地fake微信、临时数据库、禁网络pytest；原仓主题脏改动保留，没有发布、提交或推送。

入口与脚本：/home/muqiao/Documents/Codex/2026-09-27/t/work/paperclip-wechat-20260930
SpecMesh及完整审批：/home/muqiao/repo-scan/qiao-wechat/plans/2026-09-30-cli-capmesh-hardening
