# 当前进度

## Current

实施并验证微信导出修复；主Agent串行整合与部署。

## Done

- 核对当前436 HEAD、工作区、远端账户与仓库。
- 保留生产基线快照及哈希；隔离集成工作区。
- 内容主线吸收已验证的自由成稿、标题Markdown检查与来源重定向/去重改进。

## Remaining

回归已完成：publisher 122项；内容流程、固定模板与登记适配器322项、212子测试。首次手机验收发现脚注字号和默认链接样式差异，最小修复后复验通过：6套375/390门禁、6组逐节点计算样式与文字位置对照，差异为0，仅新增独立来源脚注。发行wheel干净安装验证通过。剩余哈希保护部署与推送。

## Issues / 遗留交接

- publisher的临时publish*、remote_publish_drafts.py及test_wechat_draft.py：包含顶层生产写入，部分语法错误；保留原位置，owner根Codex。最短入口为静态检查与历史原生收据对应，不能直接运行来验收。
- 内容fix_claims.py、validate_claims.py：来源批准与运行上下文尚未核实；保留原位置，owner根Codex，先关联原生run与来源证据。
- CapMesh历史计划运行JSON/lock不提交公共仓库，保留私有；owner根Codex，仅收录安全测试与有据说明。
- 已有Markdown/封面是源资产；运行状态、微信媒体ID和模型会话保持私有outbox。

## Knowledge closeout

updated：知识页《自动化开发范式与智能体协作》“内容质量与交付保真分开判断”补充源稿→投递→回读两道边界、行内空格、独立脚注核对与缺失哈希不可验收。根Codex已读回该段；不修改Memory。

## Next

通过必要回归后部署436，确认新任务使用修复源码；下一次正常草稿投递检查实际裁剪回读。
