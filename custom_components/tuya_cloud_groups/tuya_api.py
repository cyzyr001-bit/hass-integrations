"""Tuya Cloud OpenAPI client for light-group sync & control.

Correct endpoints (verified against Tuya docs + live account):
  GET  /v1.0/token?grant_type=1
  GET  /v2.1/cloud/thing/group?page_no=1&page_size=N&space_id={space_id}
       -> group list for a home ("space")
  GET  /v2.1/cloud/thing/group/{group_id}
  GET  /v2.1/cloud/thing/group/{group_id}/status-set
       -> live group state (code/name/type/value)
  POST /v2.1/cloud/thing/group/{group_id}/properties
       body = {"properties": "{\"switch_led\": true}"}   <- JSON *string*
       -> actually pushes the command to the member devices
  GET  /v2.1/cloud/thing/group/{group_id}/devices?page_no=1&page_size=N
       -> member device ids
  GET  /v2.1/cloud/thing/group/device/{device_id}
       -> which groups a device belongs to
  PUT  /v2.1/cloud/thing/group/{group_id}   body {"group_name": "..."}
       -> rename
  GET  /v1.0/devices/{device_id}            -> single device (name/status)
  GET  /v1.0/homes/{home_id}/devices        -> devices in a home
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any

import aiohttp

from .const import REGION_ENDPOINTS

_LOGGER = logging.getLogger(__name__)

TOKEN_SAFETY_MARGIN = 120  # seconds


class TuyaCloudError(Exception):
    """Generic cloud error."""


class TuyaAuthError(TuyaCloudError):
    """Authentication failed."""


class TuyaOpenApi:
    """Minimal Tuya OpenAPI client (signing per Tuya docs)."""

    def __init__(self, session: aiohttp.ClientSession, client_id: str,
                 client_secret: str, region: str = "cn"):
        self._session = session
        self._cid = client_id
        self._secret = client_secret
        self._base = REGION_ENDPOINTS.get(region, REGION_ENDPOINTS["cn"])
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._lock = asyncio.Lock()

    # ---------- signing ----------
    def _sign(self, method: str, path: str, body: str, token: str, ts: str) -> str:
        body_hash = hashlib.sha256(body.encode()).hexdigest()
        string_to_sign = f"{method}\n{body_hash}\n\n{path}"
        msg = f"{self._cid}{token}{ts}{string_to_sign}"
        return hmac.new(self._secret.encode(), msg.encode(),
                        hashlib.sha256).hexdigest().upper()

    async def _request(self, method: str, path: str, body: dict | None = None,
                       token: str | None = None, _retry: bool = True) -> Any:
        payload = json.dumps(body, separators=(",", ":")) if body else ""
        ts = str(int(time.time() * 1000))
        use_token = token if token is not None else (self._token or "")
        headers = {
            "client_id": self._cid,
            "sign": self._sign(method, path, payload, use_token, ts),
            "t": ts,
            "sign_method": "HMAC-SHA256",
            "Content-Type": "application/json",
        }
        if use_token:
            headers["access_token"] = use_token

        url = self._base + path
        try:
            async with self._session.request(
                method, url, data=payload.encode() if payload else None,
                headers=headers, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise TuyaCloudError(f"request failed: {err}") from err

        code = data.get("code")
        # token expired -> refresh once
        if code in (1010, 1011) or data.get("msg") == "token invalid":
            if _retry:
                await self._fetch_token()
                return await self._request(method, path, body, token=None, _retry=False)
        if code == 1004:
            raise TuyaCloudError("sign invalid")
        if not data.get("success", False):
            raise TuyaCloudError(f"api error {code}: {data.get('msg')}")
        return data.get("result")

    # ---------- auth ----------
    async def _fetch_token(self) -> None:
        data = await self._request("GET", "/v1.0/token?grant_type=1", token="")
        self._token = data["access_token"]
        self._token_expiry = time.time() + int(data.get("expire_time", 7200)) - TOKEN_SAFETY_MARGIN

    async def ensure_token(self) -> None:
        async with self._lock:
            if not self._token or time.time() >= self._token_expiry:
                await self._fetch_token()

    async def get(self, path: str) -> Any:
        await self.ensure_token()
        return await self._request("GET", path)

    async def post(self, path: str, body: dict | None = None) -> Any:
        await self.ensure_token()
        return await self._request("POST", path, body)

    async def put(self, path: str, body: dict) -> Any:
        await self.ensure_token()
        return await self._request("PUT", path, body)

    # ---------- groups (v2.1) ----------
    async def list_groups(self, space_id: str | int, page_size: int = 50) -> list[dict]:
        """List all light groups of one home. page_size>50 is rejected by the API."""
        out: list[dict] = []
        page = 1
        size = min(int(page_size), 50)
        while True:
            res = await self.get(
                f"/v2.1/cloud/thing/group?page_no={page}&page_size={size}"
                f"&space_id={space_id}"
            )
            rows = (res or {}).get("data_list") or []
            out.extend(rows)
            total = (res or {}).get("count", 0)
            if len(out) >= total or not rows:
                break
            page += 1
        return out

    async def get_group(self, group_id: str | int) -> dict:
        return await self.get(f"/v2.1/cloud/thing/group/{group_id}") or {}

    async def get_group_state(self, group_id: str | int) -> dict[str, Any]:
        """Live group state as {code: value}.

        v2.1 `status-set` is what the App renders.  Older/simpler groups omit
        `bright_value` here even when they support it, so callers may merge the
        v2.0 `properties` payload on top.
        """
        res = await self.get(f"/v2.1/cloud/thing/group/{group_id}/status-set") or []
        return {p["code"]: p.get("value") for p in res}

    async def get_group_properties(self, group_id: str | int) -> dict[str, Any]:
        """Live group properties from the v2.0 endpoint as {code: value}.

        Unlike `status-set` this one exposes `bright_value` for groups whose
        product mix declares it.  This endpoint is read-only.
        """
        res = await self.get(f"/v2.0/cloud/thing/group/{group_id}/properties")
        if isinstance(res, list):
            return {p["code"]: p.get("value") for p in res}
        return {}

    async def get_group_spec(self, group_id: str | int) -> dict[str, Any]:
        """Property definitions of the group as {code: type_desc}.

        Used to decide whether a group really supports brightness: a group
        built from mixed products can end up with a reduced property set and
        silently ignore `bright_value`.
        """
        res = await self.get(f"/v2.1/cloud/thing/group/{group_id}/properties") or []
        return {p["code"]: p.get("type_desc") for p in res}

    async def get_group_members(self, group_id: str | int, page_size: int = 50) -> list[dict]:
        res = await self.get(
            f"/v2.1/cloud/thing/group/{group_id}/devices?page_no=1&page_size={min(int(page_size), 50)}"
        )
        return (res or {}).get("data_list", [])

    async def get_group_capabilities(self, group_id: str | int) -> dict[str, Any]:
        """Group property spec (type ranges) as {code: type_desc}."""
        return await self.get_group_spec(group_id)

    async def set_group_properties(self, group_id: str | int, states: dict) -> bool:
        """Push a command to every device of the group (real control)."""
        body = {"properties": json.dumps(states, separators=(",", ":"))}
        res = await self.post(f"/v2.1/cloud/thing/group/{group_id}/properties", body)
        if isinstance(res, dict) and "result" in res:
            return bool(res.get("result"))
        return bool(res)

    async def rename_group(self, group_id: str | int, name: str) -> bool:
        res = await self.put(f"/v2.1/cloud/thing/group/{group_id}", {"group_name": name})
        return bool(res)

    async def groups_of_device(self, device_id: str) -> list[dict]:
        return await self.get(f"/v2.1/cloud/thing/group/device/{device_id}") or []

    # ---------- devices ----------
    async def get_device(self, device_id: str) -> dict:
        return await self.get(f"/v1.0/devices/{device_id}") or {}

    async def get_home_devices(self, home_id: str | int) -> list[dict]:
        return await self.get(f"/v1.0/homes/{home_id}/devices") or []

    async def get_user_homes(self, uid: str) -> list[dict]:
        """Every home the account belongs to (usually just one)."""
        return await self.get(f"/v1.0/users/{uid}/homes") or []

    async def get_project_devices(self, page_size: int = 10) -> list[dict]:
        """Devices bound to this cloud project (no uid needed)."""
        return await self.get(f"/v2.0/cloud/thing/device?page_size={int(page_size)}") or []

    async def get_device_state(self, device_id: str) -> dict[str, Any]:
        """Live per-device state as {code: value}."""
        dev = await self.get_device(device_id)
        return {p["code"]: p.get("value") for p in (dev.get("status") or [])}

    async def get_device_spec(self, device_id: str) -> dict[str, Any]:
        """Device thing-model spec {code: type_desc} (used to detect DP version)."""
        try:
            res = await self.get(f"/v1.0/iot-03/devices/{device_id}/specification")
            out: dict[str, Any] = {}
            for svc in ((res or {}).get("functions") or []):
                out[svc.get("code")] = svc.get("values")
            for svc in ((res or {}).get("status") or []):
                out.setdefault(svc.get("code"), svc.get("values"))
            return out
        except TuyaCloudError:
            return {}

    async def send_device_commands(self, device_id: str,
                                   commands: list[dict]) -> bool:
        """Push DPs straight to one physical device (this is what really works)."""
        res = await self.post(f"/v1.0/devices/{device_id}/commands",
                              {"commands": commands})
        return bool(res)
