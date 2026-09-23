"""Tuya Smart Web (SaaS) API client for light groups.

This talks to the same endpoint the official web App uses, which is the only
channel that can *really* drive a group's brightness:

    POST /open-api/v1.0/m/sdf/device-groups/{gid}/control
         {"product_id": "<pid>", "commands": [{"id": 3, "value": 500}]}

`id` is the numeric DP (ability id), *not* the code name.  Contrast with the
developer OpenAPI (`/v2.1/cloud/thing/group/...`) which silently accepted
brightness writes for mixed-product groups while changing nothing.

Authentication is a browser session:
    Cookie          connect.sid / SAAS_AUTH_INFO ...
    csrf-token      window.csrf from the web App (NOT the `_csrf` cookie)
    uid             account UID
    micro-app-id    the micro-app the call belongs to
    project-id      active home id   <-- required, else "微应用权限错误"
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.parse
from typing import Any

import aiohttp

from .const import (
    DEFAULT_BASE,
    DEFAULT_MAIN_APP_ID,
    DEFAULT_MICRO_APP_ID,
)

_LOGGER = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")


class TuyaWebError(Exception):
    """Raised when the web API refuses a call."""


class TuyaWebApi:
    """Async client for cn.device.tuyasmart.com."""

    def __init__(self, session: aiohttp.ClientSession, cookie: str,
                 base: str = DEFAULT_BASE,
                 micro_app_id: str = DEFAULT_MICRO_APP_ID):
        self.session = session
        self.base = base.rstrip("/")
        self.cookie = cookie.strip()
        self.micro_app_id = micro_app_id or DEFAULT_MICRO_APP_ID
        self.csrf = self._cookie_value("_csrf")
        self.uid = self._uid_from_cookie()
        self.project_id: str | None = None

    # ---------- cookie helpers ----------
    def _cookie_value(self, key: str) -> str:
        for part in self.cookie.split(";"):
            part = part.strip()
            if part.startswith(key + "="):
                return part.split("=", 1)[1]
        return ""

    def _uid_from_cookie(self) -> str:
        raw = self._cookie_value("SAAS_AUTH_INFO")
        if raw:
            try:
                return json.loads(urllib.parse.unquote(raw)).get("uid", "")
            except Exception:  # noqa: BLE001
                pass
        return ""

    # ---------- low level ----------
    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {
            "Cookie": self.cookie,
            "csrf-token": self.csrf,
            "micro-app-id": self.micro_app_id,
            "Content-Type": "application/json;charset=utf-8",
            "User-Agent": _UA,
            "Accept": "application/json, text/plain, */*",
            "Origin": self.base,
            "Referer": self.base + "/",
        }
        if self.uid:
            h["uid"] = self.uid
        if self.project_id:
            h["project-id"] = str(self.project_id)
            h["isolation-type"] = "homeId"
        if extra:
            h.update(extra)
        return h

    async def _req(self, method: str, path: str,
                   body: dict | None = None,
                   extra_headers: dict[str, str] | None = None,
                   raw: bool = False) -> Any:
        url = path if path.startswith("http") else self.base + path
        data = json.dumps(body).encode() if body is not None else None
        try:
            async with self.session.request(
                method, url, data=data, headers=self._headers(extra_headers),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                text = await resp.text()
                if raw:
                    return text
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    raise TuyaWebError(f"non-JSON reply ({resp.status}): {text[:200]}")
        except aiohttp.ClientError as err:
            raise TuyaWebError(f"request failed: {err}") from err

    # ---------- auth / session ----------
    async def refresh_csrf(self) -> str:
        """Scrape `window.csrf` from the web App shell.

        The cookie `_csrf` is *not* the right value -- the App reads the token
        from the page itself, so a stale cookie keeps writes failing with
        "invalid csrf token".
        """
        html = await self._req("GET", "/", extra_headers={
            "Accept": "text/html,application/xhtml+xml,*/*",
            "micro-app-id": DEFAULT_MAIN_APP_ID,
        }, raw=True)
        m = re.search(r'window\.csrf\s*=\s*["\']([^"\']+)', html or "")
        if not m:
            raise TuyaWebError("cannot find window.csrf -- cookie likely expired")
        self.csrf = m.group(1)
        return self.csrf

    async def ensure_session(self) -> None:
        """Validate the cookie and refresh the CSRF token."""
        if not self.cookie:
            raise TuyaWebError("no cookie configured")
        await self.refresh_csrf()
        user = await self.get_user()
        if not user or not user.get("user_id", user.get("uid")):
            # /api/smarthome/user/current returns user_id
            if not user:
                raise TuyaWebError("session invalid -- please paste a fresh cookie")
        self.uid = str(user.get("user_id") or user.get("uid") or self.uid)

    async def get_user(self) -> dict:
        r = await self._req("GET", "/api/smarthome/user/current")
        return (r or {}).get("result") or {}

    async def homes(self) -> list[dict]:
        r = await self._req("GET", "/open-api/v2.0/m/sdf/smart-home/projects/sample")
        return ((r or {}).get("result") or {}).get("homes") or []

    async def switch_home(self, home_id: str | int) -> bool:
        r = await self._req("POST", "/api/smarthome/home/active",
                            {"isolation_type": "homeId",
                             "biz_project_id": str(home_id)})
        self.project_id = str(home_id)
        return bool((r or {}).get("result"))

    # ---------- groups ----------
    async def list_groups(self, space_id: str | int | None = None) -> list[dict]:
        """All light groups of the account (or one home when space_id given)."""
        path = "/open-api/v1.0/m/sdf/device-groups"
        r = await self._req("GET", path)
        if not r or r.get("success") is False and not r.get("result"):
            # the endpoint generally ignores filters; fall back to filtering
            pass
        groups = (r or {}).get("result") or []
        if space_id is not None:
            sid = str(space_id)
            groups = [g for g in groups if str(g.get("space_id")) == sid]
        return groups

    async def get_group(self, group_id: str | int,
                        product_id: str | None = None) -> dict:
        """Group detail incl. live `dps`, member devices and room info."""
        path = f"/open-api/v1.0/m/sdf/device-groups/{group_id}"
        if product_id:
            path += f"?product_id={urllib.parse.quote(str(product_id))}"
        r = await self._req("GET", path)
        return (r or {}).get("result") or {}

    async def get_group_state(self, group_id: str | int,
                              product_id: str | None = None) -> dict:
        """Live group dps as {dp_number(str): value}."""
        info = await self.get_group(group_id, product_id)
        return info.get("dps") or {}

    async def get_group_functions(self, group_id: str | int,
                                  product_id: str) -> list[dict]:
        """Group capabilities -- the App renders its panel from this."""
        path = (f"/open-api/v1.0/m/sdf/device-groups/{group_id}/functions"
                f"?product_id={urllib.parse.quote(str(product_id))}")
        r = await self._req("GET", path)
        return (r or {}).get("result") or []

    async def get_dp_numbers(self, home_id: str | int,
                             device_id: str) -> dict[str, int]:
        """Map code -> numeric DP via the panel device model.

        Returns e.g. {"switch_led": 1, "work_mode": 2, "bright_value": 3,
        "temp_value": 4}.  Falls back to the well-known defaults.
        """
        from .const import DEFAULT_DP_NUMBERS

        path = (f"/open-api/v1.0/m/sdf/ss/panels/device/{home_id}/"
                f"{device_id}/model")
        try:
            r = await self._req("GET", path)
        except TuyaWebError as err:
            _LOGGER.debug("device model unreadable: %s", err)
            return dict(DEFAULT_DP_NUMBERS)

        defs = ((r or {}).get("result") or {}).get("deviceModelDefinition") or []
        mapping: dict[str, int] = {}
        for model in defs:
            for prop in (model.get("properties") or []):
                code = prop.get("code")
                ability = prop.get("abilityId")
                if code and isinstance(ability, int):
                    mapping[code] = ability
        out = {k: mapping.get(k, v) for k, v in DEFAULT_DP_NUMBERS.items()}
        return out

    async def set_group_dps(self, group_id: str | int, product_id: str,
                            dps: dict[str, Any]) -> bool:
        """Send raw DP numbers, exactly like the App does."""
        body = {"product_id": product_id,
                "commands": [{"id": k, "value": v} for k, v in dps.items()]}
        r = await self._req(
            "POST", f"/open-api/v1.0/m/sdf/device-groups/{group_id}/control",
            body,
        )
        ok = bool((r or {}).get("result"))
        if not ok:
            raise TuyaWebError(f"group control rejected: {r}")
        return ok


async def wait_token(session: aiohttp.ClientSession, api: TuyaWebApi) -> None:
    """Small helper: make sure CSRF is loaded before a burst of writes."""
    await api.refresh_csrf()
    await asyncio.sleep(0)
