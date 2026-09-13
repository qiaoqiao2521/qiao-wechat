from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings.

    Keep secrets in env vars. For multiple WeChat accounts, put each account in the DB
    and store secret_env_name instead of raw secret when possible.
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="WCM_", extra="ignore")

    database_url: str = "sqlite:///./data/qiao_wechat.db"
    output_dir: Path = Path("./outputs")
    upload_dir: Path = Path("./uploads")
    source_repo_dir: Path = Path("/srv/self-media-exchange/inbox/xueyu-gongzhonghao-receive")
    article_outbox_dir: Path = Path("/srv/self-media-exchange/outbox/wechat-article-files")
    video_outbox_dir: Path = Path("/srv/self-media-exchange/outbox/wechat-video-delivery")
    wechat_draft_dir: Path = Path("/srv/self-media-exchange/inbox/xueyu-gongzhonghao-receive/公众号/草稿")
    wechat_pending_dir: Path = Path("/srv/self-media-exchange/inbox/xueyu-gongzhonghao-receive/公众号/待发布")
    wechat_published_backup_dir: Path = Path("/srv/self-media-exchange/inbox/xueyu-gongzhonghao-receive/公众号/旧发布备份")
    request_timeout: float = 20.0
    token_margin_seconds: int = 300
    use_stable_token: bool = True
    allow_wechat_preview: bool = False
    allow_wechat_publish: bool = False
    app_base_url: str = "http://127.0.0.1:8000"
    default_author: str = ""
    render_theme: str = "wechat_baseline"
    publish_policy: Literal["draft_only", "preview_then_submit", "submit_direct"] = "draft_only"
    kroki_base_url: str = "https://kroki.io"
    latex_render_base_url: str = "https://latex.codecogs.com/svg.latex"

    def ensure_dirs(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.article_outbox_dir.mkdir(parents=True, exist_ok=True)
        self.video_outbox_dir.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite:///"):
            db_path = Path(self.database_url.replace("sqlite:///", ""))
            db_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Directory creation is intentionally not done here: importing the package must
    # work on clean machines where deployment paths (/srv/...) do not exist yet.
    # Entry points (init_db via app lifespan / CLI) call ensure_dirs() explicitly.
    return Settings()


@lru_cache(maxsize=1)
def get_plain_env() -> dict[str, str]:
    env_file = Path(".env")
    if not env_file.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def get_env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    return get_plain_env().get(name)
