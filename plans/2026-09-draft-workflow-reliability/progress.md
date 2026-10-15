# Progress

## 2026-10-15：复用渲染器与绑定包入口

- 内容仓已只读查明：132曾成功套固定数学模板，但最终另一份外部HTML入库，未保留Markdown关联。渲染器和99套CSS一直存在；问题是交接绕行，不新增渲染引擎。
- 新增`html-draft --package-dir`薄适配，复用原create_html_draft／prepare／投递生命周期；校对既有包与当前源稿，在初始文章同时保留Markdown、HTML及受限模板身份。普通HTML入口兼容，更新已有成品仍用原位维护流程。
- 固定模板ID／版本两个属性参与已有HTML整理保留，其他编辑器私有data仍删除。工作台模板目录由内容仓直接导入ThemeManager和renderer，按需单样例，不启动新publisher服务。
- 已验：包入口／原CLI／publisher／草稿可靠性／微信导出／HTML整理共87个用例通过；修复保留Markdown后HTML排版变化被内容身份忽略的问题。原HTML API两个测试模块在本机缺httpx，未安装且未冒称已测；默认参数兼容并由publisher既有测试覆盖。没有执行真实微信投递或回读。
- 已交付：两仓代码已普通push，436 publisher ecc82ef／内容c76b5c0部署；既有publisher6个未提交文件与内容54个文件哈希不变，130–134数据库行不变。正式工作台模板浏览器验证通过，新CLI help与132真实旧包纯读取成功，没有执行投递。内容仓plans/wechat-workbench-20261011为主任务记录。
- 知识收尾：already_covered；复用既有实现与源稿／渲染产物分别绑定的原则已由现有知识页覆盖，未新增重复知识页。

## Current

2026-09-14（第三轮）：F24 缺口审计中除插件外的 4 项全部补齐并回归（83 pytest / 19 runner / wheel 127 文件）。深色模拟预览、98 套主题（xiaohu 导入 85）、draft-batch 批量草稿、XHS 卡片规划。剩余：真实账号远端验收（需当次授权）。

## Done

- 2026-09-07/10：计划与 SpecMesh 文档接入（见下）。
- 阶段 0：隔离环境（临时 DB/outputs/uploads/outbox）+ 基线测试 49 项全过（安装 ImageMagick 后，5 项历史失败均为环境缺 `convert`）。
- 阶段 1（本地）：wheel 修复——`services` 子包与 `themes/*.css|json` 入包（13→42 文件）；目录创建移出 import；仓库外 venv 安装验证 import/渲染/CLI init 通过。
- 阶段 2（本地）：故障注入复现（失败不留痕/超时未知不可辨/重复草稿）；实现 `request_key` 幂等、unknown 结果状态、服务内即时 commit + 独立会话兜底、`_ensure_schema_columns` 旧库迁移、`--force-new`/API `force_new`；新增 `tests/test_draft_reliability.py` 7 项。
- 阶段 3（本地）：`examples/regression/` 十类样本（中文/标题/列表/表格/代码/引用callout/长链接/PlantUML+Graphviz图表/公式/透明图/缺失图/mermaid 边界）；三代表主题 × 375/430px 浏览器实测（截图 + DOM 溢出扫描）；修复代码块微信端裁剪（13 主题 pre-wrap）、WeMD 主题 pre 强制撑宽、预览相对图片缺图、HTML head 样式丢弃；`tests/test_sample_regression.py` 9 项。
- 回归交付：`scripts/run_sample_regression.py`（隔离临时环境，19 项检查全过，输出 `outputs/regression/` 报告+预览）；全套 pytest 65 项通过；wheel 重建并在干净安装上验证新行为。
- 阶段 4：`/tools/html-draft` 新增结果徽标（新建/复用）、错误分类卡片（是否可能已提交 + 下一步）、强制新建开关、任务记录查询（失败自动附 article_id 并加载）；from-html 路由 force_new 透传；CLI draft/html-draft 友好错误（含草稿箱核对 + --force-new 指引）、复用提示、jobs 显示 error_category；浏览器（mock 服务）实测成功/复用/force_new/真实拒绝四条路径。`scripts/preflight_remote_acceptance.py`（只读预检 + 远端验收清单）。tests/test_operation_feedback.py 6 项。
- 文档：DECISIONS D10–D14、ARCHITECTURE 数据/不变量/脆弱区更新、USAGE 幂等与 force-new/error_category 说明。

## Remaining

- 阶段 5：真实账号两篇草稿（Markdown + HTML）远端验收——需当次授权与账号环境；运行 `scripts/preflight_remote_acceptance.py` 核对就绪状态后按清单执行。
- 旧库升级演练：用脱敏生产 DB 副本跑一次 `_ensure_schema_columns` 迁移（本地已用构造的 legacy 库测试）。

## Issues

- 导入主题为机器转换（JSON→CSS），样式完整但未逐套人工目检；人工挑选主题时建议先看画廊页。深色预览是反相近似，非微信精确渲染。

- morandi_forest 等主题的 `min-width:max-content` 修复只覆盖 pre 代码块；若其它 WeMD 主题元素出现同类窄屏问题需按样本回归扩展。
- 进程内 per-key 锁不覆盖多进程部署；幂等在多进程下依赖 request_key 持久记录（提交窗口仍非跨进程原子），已在 ARCHITECTURE 标注。
- mermaid 仍不渲染（源码保留为代码块）；如需支持要新增 kroki mermaid 分支与测试。
- F23 教训：任何注入测试不得直接实例化真实 `WeChatApiClient`；已通过 mock fixture 固化。

## Next

阶段 4：基于现有 `error_category`/`draft_outcome` 契约更新 `/tools/html-draft` 页面与 CLI 输出，使失败后无需读 traceback 即可判断下一步。
