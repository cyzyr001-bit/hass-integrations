"""Coordinator for Tuya WebGroups.

Polls each group through the *web* (SaaS) API and writes through the same
channel, so brightness works even for groups whose members come from different
products (the developer OpenAPI does not expose `bright_value` for those).

Read  : GET  /open-api/v1.0/m/sdf/device-groups/{gid}?product_id=...
        -> result.dps  {"1": false, "2": "white", "3": 500, "4": 0, ...}
Write : POST /open-api/v1.0/m/sdf/device-groups/{gid}/control
        {"product_id": ..., "commands": [{"id": 3, "value": 500}]}

The numeric DP ids are resolved per group from the panel device model
(`/v1.0/m/sdf/ss/panels/device/{home}/{dev}/model`, field `abilityId`).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    DEFAULT_DP_NUMBERS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    DP_BRIGHT,
    DP_SWITCH,
    DP_TEMP,
    DP_WORK_MODE,
)
from .web_api import TuyaWebApi, TuyaWebError

_LOGGER = logging.getLogger(__name__)


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes", "on")


def _as_int(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


class TuyaWebGroupCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Poll the web API for every light group of the home."""

    def __init__(self, hass: HomeAssistant, api: TuyaWebApi,
                 groups: dict[str, dict], home_id: str,
                 scan_interval: int = DEFAULT_SCAN_INTERVAL):
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} groups",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.api = api
        self.home_id = str(home_id)
        # {group_id: {"name", "product_id", "members", "dps": {code: num}}}
        self.groups = groups

    # ---------- polling ----------
    async def _read_group(self, gid: str, info: dict) -> dict[str, Any]:
        pid = info.get("product_id")
        dps = await self.api.get_group_state(gid, pid)
        if not dps:
            raise TuyaWebError(f"group {gid} returned no dps")

        nums = info.get("dps") or DEFAULT_DP_NUMBERS

        def val(code: str) -> Any:
            return dps.get(str(nums.get(code, DEFAULT_DP_NUMBERS[code])))

        bright = _as_int(val(DP_BRIGHT))
        return {
            "switch_led": _as_bool(val(DP_SWITCH)),
            "bright_value": bright,
            "temp_value": _as_int(val(DP_TEMP)),
            "work_mode": val(DP_WORK_MODE),
            "supports_brightness": bright is not None,
        }

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        sem = asyncio.Semaphore(4)

        async def guarded(gid: str, info: dict):
            async with sem:
                try:
                    return gid, await self._read_group(gid, info)
                except TuyaWebError as err:
                    errors.append(f"{gid}: {err}")
                except Exception as err:  # noqa: BLE001
                    errors.append(f"{gid}: {err!r}")
                return gid, None

        for gid, state in await asyncio.gather(
            *(guarded(g, i) for g, i in self.groups.items())
        ):
            if state is not None:
                result[gid] = state

        if not result and errors:
            raise UpdateFailed("; ".join(errors[:3]))
        if errors:
            _LOGGER.debug("Some groups failed: %s", errors)
        return result

    # ---------- control ----------
    async def async_set_states(self, group_id: str, states: dict[str, Any]) -> None:
        """Turn HA's light calls into group DP commands."""
        info = self.groups.get(group_id) or {}
        pid = info.get("product_id")
        nums = info.get("dps") or DEFAULT_DP_NUMBERS
        dps: dict[int, Any] = {}

        if DP_SWITCH in states:
            dps[nums[DP_SWITCH]] = bool(states[DP_SWITCH])
        if DP_BRIGHT in states:
            dps[nums[DP_BRIGHT]] = max(10, min(int(states[DP_BRIGHT]), 1000))
            dps.setdefault(nums[DP_WORK_MODE], "white")
        if DP_TEMP in states:
            dps[nums[DP_TEMP]] = max(0, min(int(states[DP_TEMP]), 1000))
            dps.setdefault(nums[DP_WORK_MODE], "white")

        if not dps:
            return
        if not pid:
            raise TuyaWebError(f"group {group_id} has no product_id")

        await self.api.set_group_dps(group_id, pid, dps)
        _LOGGER.debug("group %s <- %s", group_id, dps)

        # optimistic update so the UI reacts immediately
        cur = self.data.get(group_id)
        if cur is not None:
            if DP_SWITCH in states:
                cur["switch_led"] = bool(states[DP_SWITCH])
            if DP_BRIGHT in states:
                cur["bright_value"] = int(states[DP_BRIGHT])
            if DP_TEMP in states:
                cur["temp_value"] = int(states[DP_TEMP])
            self.async_set_updated_data(self.data)
