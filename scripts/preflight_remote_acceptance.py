#!/usr/bin/env python3
"""阶段 5 真实账号验收的本地预检（只读，不发起任何微信接口调用）。

检查本地就绪条件并打印远端验收清单。真正的草稿创建需要：
1. 明确的当次授权（本脚本绝不代替授权）；
2. 账号 secret 可用（环境变量或 .env）；
3. 人工按清单核对公众号后台。

用法：uv run python scripts/preflight_remote_acceptance.py [--account-name NAME]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

CHECKLIST = """
远端验收清单（需当次授权 + 账号环境就绪后人工执行）：
  1. 用真实账号提交一篇 Markdown 文章草稿（CLI draft 或 API），记录 media_id。
  2. 用真实账号提交一篇外部 HTML 草稿（html-draft / from-html-file），记录 media_id。
  3. 公众号后台「草稿箱」人工核对两篇：标题/作者/摘要/封面/正文/图片显示一致，
     长代码行不裁剪、表格不撑破、图表与公式正常显示。
  4. 记录账号当时的权限证据：draft/add 成功；手机预览（如尝试）返回码与日期，
     不把历史 OPS 记录当现行能力。
  5. 失败场景在真实环境至少观察一次（如断网超时），确认任务记录出现
     error_category=remote_result_unknown 且盲重发被拦截。
  6. 不发布、不群发；验收产物不含真实正文与凭据。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-name", default=None, help="只检查指定名称的账号")
    args = parser.parse_args()

    import os

    run_root = Path(os.environ.get("WCM_PREFLIGHT_TMP", REPO_ROOT))
    ok = True
    failures: list[str] = []

    from qiao_wechat.config import get_env_value, get_settings
    from qiao_wechat.db import db_session, init_db
    from qiao_wechat.models import WeChatAccount
    from qiao_wechat.services.renderer import WeChatMarkdownRenderer

    settings = get_settings()
    print("== 本地就绪检查（只读） ==")

    # 1. 数据库可用且有账号
    try:
        init_db()
        with db_session() as db:
            query = db.query(WeChatAccount)
            if args.account_name:
                query = query.filter(WeChatAccount.name == args.account_name)
            accounts = query.all()
        if not accounts:
            failures.append("没有可用账号：先用 `qiao-wechat add-account` 或 API 创建")
            print(f"[FAIL] 账号：无（{args.account_name or '任意账号'}）")
        else:
            for acct in accounts:
                secret_ready = bool(acct.raw_secret) or (
                    acct.secret_env_name and bool(get_env_value(acct.secret_env_name))
                )
                status = "OK" if secret_ready else "FAIL（secret 不可解析）"
                if not secret_ready:
                    failures.append(f"账号 {acct.name} 的 secret_env_name={acct.secret_env_name} 无法解析到值")
                print(f"[{status}] 账号 {acct.name} appid={acct.appid[:6]}*** draft能力以远端验收为准")
    except Exception as exc:  # noqa: BLE001
        ok = False
        failures.append(f"数据库不可用：{exc}")
        print(f"[FAIL] 数据库：{exc}")

    # 2. 安全开关状态
    print(f"[{'OK' if settings.publish_policy == 'draft_only' else 'WARN'}] publish_policy={settings.publish_policy}")
    print(f"[{'OK' if not settings.allow_wechat_preview else 'WARN'}] allow_wechat_preview={settings.allow_wechat_preview}")
    print(f"[{'OK' if not settings.allow_wechat_publish else 'WARN'}] allow_wechat_publish={settings.allow_wechat_publish}")
    if settings.allow_wechat_preview or settings.allow_wechat_publish:
        print("      ↑ 远端验收只需草稿能力；预览/发布开关如非必要请保持关闭。")

    # 3. 主题与样本
    themes = WeChatMarkdownRenderer.available_themes()
    print(f"[{'OK' if 'wechat_baseline' in themes else 'FAIL'}] 主题资源：{len(themes)} 个（含 wechat_baseline={ 'wechat_baseline' in themes }）")
    sample = REPO_ROOT / "examples" / "regression" / "sample_full.md"
    print(f"[{'OK' if sample.is_file() else 'FAIL'}] 回归样本：{sample}")
    if not sample.is_file():
        failures.append("回归样本缺失，先在仓库根目录运行")

    # 4. 本地回归状态
    report = REPO_ROOT / "outputs" / "regression" / "REGRESSION_REPORT.md"
    if report.is_file():
        print(f"[OK] 本地回归报告：{report}")
    else:
        print("[WARN] 尚未生成本地回归报告：`uv run python scripts/run_sample_regression.py`")

    print(CHECKLIST)
    if failures:
        print("预检未通过：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("本地就绪检查通过。远端验收请按上方清单在获得当次授权后执行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
