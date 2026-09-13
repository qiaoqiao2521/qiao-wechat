# Findings

2026-09-07 基线：`545f592200cd7cf0583fb7ad3874abf8e17d0000`。来源均为本次读取的仓库文件；未运行应用。

| ID | 已观察事实 | 来源 | 对计划的影响 |
|---|---|---|---|
| F01 | 根计划是 `wx-content-mesh-python → qiao-wechat` rename，metadata/docs 与 verification 尚未勾选 | 原 `task_plan.md / progress.md / findings.md` | 核对后归档到 `plans/2026-09-rename-archive/`，不把旧状态当作当前缺陷证明 |
| F02 | 原仓库无 AGENTS/PROJECT/CLAUDE 与 docs 长期入口 | tracked 文件列表 | 需要有针对性的 SpecMesh 接入（已在本次完成） |
| F03 | Python ≥ 3.11，版本 0.3.0；显式 `packages` 仅 `qiao_wechat` | `pyproject.toml` | wheel 子包/资源缺失是疑点，尚未构建确认 |
| F04 | HTML 入站设置 `content_ingress=html`，prepare 后以 `upload_inline_images=False` 走草稿服务 | `services/publisher.py` | 固定 HTML 不重渲染契约 |
| F05 | `create_wechat_draft` 的 `_job` 晚于图片/封面准备；`except` 设置 failed 后 `raise` | `services/publisher.py` | 准备失败可能缺操作记录；需扩范围验证 |
| F06 | API `get_db` 及 `db_session` 异常都会 rollback | `app.py`、`db.py` | F05 的失败字段可能不持久化；尚未动态复现 |
| F07 | 草稿路径直接 `add_draft`，`MediaAsset` 有账号/hash/用途唯一键 | `publisher.py`、`models.py` | 素材去重不等于文章幂等；并发与超时窗口需设计 |
| F08 | publish/preview 默认关闭，`draft_only`，WCM_ 前缀；初始化创建 outbox 目录 | `config.py` | 保留兼容；测试必须隔离所有会创建的目录 |
| F09 | SVG 转换调用 ImageMagick `convert`；有 `requests` 下载 | `image_service.py` | 系统依赖与错误定位属于真实交付问题 |
| F10 | README 泛称图形支持，测试中存在 Mermaid 源码保留断言 | 原 `README.md`、`tests/test_publisher.py` | 逐类型核对，不断言已全支持或全部不可用 |
| F11 | 账号限制记录日期 2026-04-28，个人未认证号 draft 可用、手机预览 48001 | `OPS.md` | 只作历史事实，不推广为全平台现行规定 |
| F12 | 发布草稿与源文件归档是分开的函数 | `wechat_repo_flow.py` | 保持草稿/发布/归档三个语义 |
| F13 | README 指向上游个人 IP 工作流，但此仓 `skills/` 目录仅含科研类附带能力 | 原 `README.md`、`skills/README.md` | 内容工作流属于外部依赖，不机械复制所有上游 skill |
| F14 | README 命令端口混用 8000/8001 且有机器绝对链接 | 原 `README.md` | 已下沉到 `docs/USAGE.md`；新 README 用启动命令端口为准，并保留端口混用说明 |
| F15 | 调度器为 `BackgroundScheduler`，进程需存活 | `scheduler.py` | 文档保留边界，不默认升级持久队列 |
| F16 | FastAPI 共 24 条 `@app.*` 路由，主题画廊/管理 API 与 `/tools/*` 同进程 | `qiao_wechat/app.py` | 文档不再按"主题服务独立 :8001"描述 |
| F17 | `themes/metadata.json` 把 `wechat_baseline / wemd_clean / wemd_card / wechat_standard_v2` 标 source=`qiao-wechat`；`default / grace / simple / modern` 来自 local wechat-format；`academic_paper / bauhaus / knowledge_base / morandi_forest / receipt` 来自 WeMD | `themes/metadata.json` | USAGE 主题列表与来源描述以 `metadata.json` 为准 |

| F18 | 2026-09-14 执行轮（基线 545f592 + 本地未提交 SpecMesh 文档）。wheel 实测仅 13 个文件：`services/` 0 个模块、`themes/` 0 个资源；干净安装上 `import qiao_wechat` 即崩溃（`db.py` import 期创建 `/srv/...` 目录被拒） | `uv build` + 仓库外 venv 安装 | F03 疑点坐实；已修复 pyproject 与目录创建时机（D10–D14 见 DECISIONS） |
| F19 | 故障注入复现（mock 客户端，patch 模块级 `WeChatApiClient`）：远端明确拒绝 + 外层回滚 → `Article` 行本身消失、0 条 job；超时与明确失败不可区分；两次调用 `add_draft` 2 次 | `/tmp` 复现脚本，已固化为 `tests/test_draft_reliability.py` | F05/F06/F07 动态坐实；修复后：失败留痕、unknown 拦截盲重发、request_key 复用 |
| F20 | 375px 浏览器实测（wechat_baseline/default/morandi_forest × 375/430px）：全部主题代码块 `white-space:pre`，长代码行在微信端必然被裁剪；morandi 非 hljs 代码还继承 UA `pre` 默认值且 WeMD 系主题 `pre` 有 `min-width:max-content` 强制撑宽（scrollW 535px） | 本地预览 + 浏览器 evaluate 溢出扫描 | 已统一 `pre-wrap + break-all` 并移除 max-content；复测 6 组合无溢出 |
| F21 | 本地预览不复制相对路径图片 → 预览必然缺图（`assets/transparent.png` 未加载）；HTML 入口 `<head><style>` 类样式被整体丢弃（normalize 只取 body，微信不支持 style 标签） | 浏览器图片加载检查 + normalize 输出比对 | 已实现预览图片物化（哈希文件名复制到 `outputs/article_N/assets/`）与 head 样式规则内联（D13/D14） |
| F22 | 幂等键设计实施中的两个坑：(1) revision 若在素材 URL 替换后计算则每次不同（mmbiz URL 非确定性）→ 必须在替换前、基于稳定输入打戳；(2) 独立会话兜底重建的 job 若不回填 request_key，未知结果防护会被绕过 | 修复期间 pytest 失败定位 | 键稳定性与兜底回填均有回归测试锁定 |
| F23 | 测试注入必须 patch 模块级 `WeChatApiClient`；曾误 patch 实例属性导致真实请求发到 api.weixin.qq.com（appid 无效被 40013 拒绝，未产生副作用） | 复现脚本第一版 | 回归一律用 `monkeypatch.setattr(publisher_module, "WeChatApiClient", ...)`；runner 用本地 mock 客户端 |

## 待验证而非已确认缺陷

~~发行包资源、API 失败持久化、完整双入口行为、Mermaid 处理、并发/重试语义~~（发行包/失败持久化/双入口/重复调用已于本轮验证；Mermaid 边界已固定）。仍未验证：真实账号远端验收（draft/手机预览权限）、多进程部署下的幂等窗口、生产 `/srv` 环境的实际升级。

## 本次建议

将干净安装、可靠草稿交付与失败恢复置于新增主题/后台/选题系统之前。~~请求键和未知结果状态为拟议设计，尚未进入代码或数据库~~（已于 2026-09-14 实施并通过回归：`request_key`/`error_category` 列 + `draft_outcome` meta，见 DECISIONS D10–D14 与 `tests/test_draft_reliability.py`）。

## 执行中记录规则

追加事实需写基线、复现路径、观察结果和限制。不要保存凭据、真实接口 token 或整篇私有文章。跨任务成立的事实才晋升到架构/决策；普通修复历史留给 Git。
| F24 | 2026-09-14 用户要求按参考仓库借项表逐项审计。结论：22 项借项已借 17 项；真实缺口 5 项——深色模式预览（WeMD）、插件系统（HyperClockUp，建议不借）、主题数量（xiaohu 30 vs 13，可 import_css 补）、批量创建草稿（wechat-publisher，只借草稿不借发布）、XHS 图片卡片规划（Non-goal 维持）。超出参考仓库处：幂等/unknown、双入口不重渲染契约 | 代码 grep + creative_pipeline/theme_manager/wechat_client 逐一取证 | 缺口 1/3/4 为候选实施项，等用户点单 |
| F25 | 2026-09-14 缺口补齐实施。xiaohu-wechat-format 主题为 JSON 描述（styles 按元素键、snake_case 属性），85 套全部经适配器导入（`xh_` 前缀）。实施中修掉三个适配器 bug：snake→kebab 转换误用驼峰（产生非法属性 fontsize 等）、根规则剥离后缺 font-size、个别主题无 wrapper 键。根样式契约测试（body-first）拦截了前两轮错误导入 | scripts/import_external_themes.py + 结构验证（渲染样本断言内联样式数、font-size/margin-bottom 合法属性、table/pre 在位） | 主题=数据路线可行；新增主题须过 71+ 项既有回归 |
