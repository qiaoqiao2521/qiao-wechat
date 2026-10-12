from __future__ import annotations

import json
import mimetypes
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from sqlalchemy.orm import Session

from ..config import get_env_value, get_settings
from ..models import AccessToken, WeChatAccount


class WeChatError(RuntimeError):
    def __init__(self, message: str, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.payload = payload or {}


class WeChatApiClient:
    """Small WeChat Official Account API client.

    It deliberately handles only the content-publishing endpoints that wxAiPost-like
    backends need: token cache, cover upload, inline image upload, draft, preview,
    publish submit and publish status polling.
    """

    base_url = "https://api.weixin.qq.com"

    def __init__(self, session: Session, account: WeChatAccount):
        self.session = session
        self.account = account
        self.settings = get_settings()

    @property
    def appid(self) -> str:
        return self.account.appid

    @property
    def secret(self) -> str:
        if self.account.raw_secret:
            return self.account.raw_secret
        if self.account.secret_env_name:
            value = get_env_value(self.account.secret_env_name)
            if value:
                return value
        raise WeChatError(f"No secret configured for account {self.account.name}")

    def get_access_token(self, *, force_refresh: bool = False) -> str:
        token_type = "stable" if self.settings.use_stable_token else "basic"
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cached = (
            self.session.query(AccessToken)
            .filter(AccessToken.account_id == self.account.id, AccessToken.token_type == token_type)
            .one_or_none()
        )
        if not force_refresh and cached and cached.expires_at > now + timedelta(seconds=self.settings.token_margin_seconds):
            return cached.access_token

        payload = self._fetch_token(token_type=token_type, force_refresh=force_refresh)
        access_token = payload.get("access_token")
        if not access_token:
            raise WeChatError("access_token missing in response", payload)
        expires_in = int(payload.get("expires_in") or 7200)
        expires_at = now + timedelta(seconds=max(60, expires_in - self.settings.token_margin_seconds))
        if cached:
            cached.access_token = access_token
            cached.expires_at = expires_at
            cached.raw_response = payload
        else:
            cached = AccessToken(
                account_id=self.account.id,
                token_type=token_type,
                access_token=access_token,
                expires_at=expires_at,
                raw_response=payload,
            )
            self.session.add(cached)
        self.session.flush()
        return access_token

    def upload_inline_image(self, image_path: str | Path) -> dict[str, Any]:
        """Upload image used inside article body.

        Returns {'url': 'https://mmbiz.qpic.cn/...'}.
        This URL is for article body HTML, not for cover thumb_media_id.
        """
        token = self.get_access_token()
        path = Path(image_path)
        self._validate_upload_image(path, allowed={"image/jpeg", "image/png"}, max_bytes=1 * 1024 * 1024, label="inline image")
        url = f"{self.base_url}/cgi-bin/media/uploadimg"
        with path.open("rb") as fp:
            resp = requests.post(
                url,
                params={"access_token": token},
                files={"media": (path.name, fp)},
                timeout=self.settings.request_timeout,
            )
        return self._checked_json(resp)

    def upload_permanent_image(self, image_path: str | Path) -> dict[str, Any]:
        """Upload cover image as permanent material; returns media_id and url."""
        token = self.get_access_token()
        path = Path(image_path)
        self._validate_upload_image(
            path,
            allowed={"image/bmp", "image/png", "image/jpeg", "image/gif"},
            max_bytes=10 * 1024 * 1024,
            label="permanent image",
        )
        url = f"{self.base_url}/cgi-bin/material/add_material"
        with path.open("rb") as fp:
            resp = requests.post(
                url,
                params={"access_token": token, "type": "image"},
                files={"media": (path.name, fp)},
                timeout=self.settings.request_timeout,
            )
        return self._checked_json(resp)

    def add_draft(self, articles: list[dict[str, Any]]) -> dict[str, Any]:
        token = self.get_access_token()
        url = f"{self.base_url}/cgi-bin/draft/add"
        resp = requests.post(
            url,
            params={"access_token": token},
            data=json.dumps({"articles": articles}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=self.settings.request_timeout,
        )
        return self._checked_json(resp)

    def get_draft(self, media_id: str) -> dict[str, Any]:
        token = self.get_access_token()
        url = f"{self.base_url}/cgi-bin/draft/get"
        resp = requests.post(url, params={"access_token": token}, json={"media_id": media_id}, timeout=self.settings.request_timeout)
        return self._checked_json(resp)

    def preview_mpnews(self, media_id: str, *, touser_openid: str | None = None, towxname: str | None = None) -> dict[str, Any]:
        """Send a preview message to a user's phone.

        This is not a public preview URL. Use touser_openid when possible; towxname is
        convenient for internal testing if the account permits it.
        """
        if not touser_openid and not towxname:
            raise ValueError("touser_openid or towxname is required")
        token = self.get_access_token()
        url = f"{self.base_url}/cgi-bin/message/mass/preview"
        payload: dict[str, Any] = {"msgtype": "mpnews", "mpnews": {"media_id": media_id}}
        if touser_openid:
            payload["touser"] = touser_openid
        if towxname:
            payload["towxname"] = towxname
        resp = requests.post(url, params={"access_token": token}, json=payload, timeout=self.settings.request_timeout)
        return self._checked_json(resp)

    def submit_freepublish(self, media_id: str) -> dict[str, Any]:
        token = self.get_access_token()
        url = f"{self.base_url}/cgi-bin/freepublish/submit"
        resp = requests.post(url, params={"access_token": token}, json={"media_id": media_id}, timeout=self.settings.request_timeout)
        return self._checked_json(resp)

    def get_publish_status(self, publish_id: str) -> dict[str, Any]:
        token = self.get_access_token()
        url = f"{self.base_url}/cgi-bin/freepublish/get"
        resp = requests.post(url, params={"access_token": token}, json={"publish_id": publish_id}, timeout=self.settings.request_timeout)
        return self._checked_json(resp)

    def get_published_article(self, article_id: str) -> dict[str, Any]:
        token = self.get_access_token()
        url = f"{self.base_url}/cgi-bin/freepublish/getarticle"
        resp = requests.post(url, params={"access_token": token}, json={"article_id": article_id}, timeout=self.settings.request_timeout)
        return self._checked_json(resp)

    def mass_send_all(self, media_id: str) -> dict[str, Any]:
        """Mass-send mpnews to all users.

        This is intentionally not called by the safe default workflow. Prefer creating
        a draft and previewing first.
        """
        token = self.get_access_token()
        url = f"{self.base_url}/cgi-bin/message/mass/sendall"
        payload = {"filter": {"is_to_all": True}, "mpnews": {"media_id": media_id}, "msgtype": "mpnews", "send_ignore_reprint": 0}
        resp = requests.post(url, params={"access_token": token}, json=payload, timeout=self.settings.request_timeout)
        return self._checked_json(resp)

    def _fetch_token(self, *, token_type: str, force_refresh: bool) -> dict[str, Any]:
        if token_type == "stable":
            url = f"{self.base_url}/cgi-bin/stable_token"
            resp = requests.post(
                url,
                json={
                    "grant_type": "client_credential",
                    "appid": self.appid,
                    "secret": self.secret,
                    "force_refresh": force_refresh,
                },
                timeout=self.settings.request_timeout,
            )
            return self._checked_json(resp)
        url = f"{self.base_url}/cgi-bin/token"
        resp = requests.get(
            url,
            params={"grant_type": "client_credential", "appid": self.appid, "secret": self.secret},
            timeout=self.settings.request_timeout,
        )
        return self._checked_json(resp)

    @staticmethod
    def _checked_json(resp: requests.Response) -> dict[str, Any]:
        # WeChat JSON stays UTF-8 when requests infers Latin-1 from text/plain.
        # Use the same decoding as the daily draft readback.
        resp.encoding = "utf-8"
        try:
            payload = resp.json()
        except Exception as exc:  # pragma: no cover - depends on remote
            raise WeChatError(f"WeChat HTTP {resp.status_code}: non-json response {resp.text[:200]}") from exc
        errcode = payload.get("errcode")
        if errcode not in (None, 0, "0"):
            raise WeChatError(f"WeChat API error {errcode}: {payload.get('errmsg', '')}", payload)
        return payload

    @staticmethod
    def _validate_upload_image(path: Path, *, allowed: set[str], max_bytes: int, label: str) -> None:
        mime = mimetypes.guess_type(path.name)[0] or ""
        if mime not in allowed:
            allowed_text = ", ".join(sorted(allowed))
            raise ValueError(f"WeChat {label} must be one of: {allowed_text}; got {mime or 'unknown'}")
        size = path.stat().st_size
        if size > max_bytes:
            raise ValueError(f"WeChat {label} is too large: {size} bytes > {max_bytes} bytes")
