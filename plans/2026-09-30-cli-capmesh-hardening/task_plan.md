# CLI CapMesh 加固验收

## Goal
通过 Paperclip 让 Codex CLI 派发 AGY、ZCode、CBC，本地加固草稿未知结果/重复调用/CLI 反馈契约；派发后结束模型运行，三份交接后事件唤醒 Codex 审批。

## Scope
独立工作副本；仅本地 fake WeChat client、临时数据库和输出。保留原工作树未提交主题改动。不发真实草稿、不发布、不提交推送。无需全仓改造。

## Steps
1. 固化基线、CapMesh 实验、三个有界子任务。
2. Codex 创建子任务，退出推理；不做模型状态轮询。
3. 三个 CLI 交付 SpecMesh 文件与执行证据，失败同样如实返回。
4. 原生事件唤醒 Codex，独立复跑、逐项接受/打回/拒绝；最多一轮有依据纠偏。

## Success
有实际 CLI 进程及产物；等待期间协调 Codex 没有模型 run；三份交接齐备后产生独立审批。交接 done 不等于加固 PASS。

## Current
已完成独立审批：AGY/CBC ACCEPT，ZCode REJECT；接受的新测试合并验证15项通过并复制到主仓，详见review.md与approval.json。

## Adopted reference
Obsidian Wiki/自动化开发范式与智能体协作.md 的按任务选择验收依据：进程完成与产品验收分开。
