"""Coordinator for Tuya Cloud Groups — group-level control only.

Everything goes through the Tuya *group* so the cloud fans the command out to
the members itself and the mobile App stays perfectly in sync.  Nothing is ever
pushed to a member device directly.

Control matrix (measured live against the account, 2026-09-13)
--------------------------------------------------------------

    POST /v2.1/cloud/thing/group/{gid}/properties
      body = {"properties": "<json string>"}

    property        range        notes
    --------------  -----------  -------------------------------------------
    switch_led      bool         instant, group shadow updates at once
    temp_value      0..1000      instant, colour temperature
    bright_value    10..1000     instant, brightness (App % = value / 10)
    work_mode       enum         send "white" together with brightness

Reading the group state
-----------------------

    GET /v2.1/cloud/thing/group/{gid}/status-set                  (preferred)
    GET /v2.0/cloud/thing/group/{gid}/properties                  (fallback)

Both expose `switch_led` / `temp_value` / `work_mode`; the newer endpoint also
returns `bright_value` -- but only when the group actually declares that
property.  A group built from mixed products can end up with a *reduced*
property set (e.g. 12 instead of 17 properties) that has no `bright_value` at
all; such a group silently ignores brightness writes.  We therefore advertise
brightness support only when the group's own property list contains it.
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
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    DP_BRIGHT,
    DP_SWITCH,
    DP_TEMP,
    DP_WORK_MODE,
    GROUP_COLOUR_MODES,
)
from .tuya_api import TuyaCloudError, TuyaOpenApi

_LOGGER = logging.getLogger(__name__)

# every property the group API can write at group level
GROUP_WRITABLE = {"switch_led", "temp_value", "bright_value", "work_mode",
                  "scene_data", "countdown", "do_not_disturb", "switch_gradient"}


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes", "on")


def _as_int(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


class TuyaGroupCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Poll Tuya group shadows — the exact view the mobile App renders."""

    def __init__(self, hass: HomeAssistant, api: TuyaOpenApi,
                 groups: dict[str, dict], scan_interval: int = DEFAULT_SCAN_INTERVAL):
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} groups",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.api = api
        # {group_id: {"name": str, "members": [...], "brightness": bool, ...}}
        self.groups = groups

    # ---------- polling ----------
    async def _read_group(self, gid: str, info: dict) -> dict[str, Any]:
        props: dict[str, Any] = {}
        try:
            props = await self.api.get_group_state(gid) or {}
        except TuyaCloudError as err:
            _LOGGER.debug("group %s status-set failed: %s", gid, err)

        # the v2.0 endpoint carries `bright_value` for groups that declare it
        if info.get("brightness"):
            try:
                extra = await self.api.get_group_properties(gid) or {}
                for code, value in extra.items():
                    props.setdefault(code, value)
                if extra.get("bright_value") is not None:
                    props["bright_value"] = extra["bright_value"]
            except TuyaCloudError as err:
                _LOGGER.debug("group %s v2.0 properties failed: %s", gid, err)

        if not props:
            raise TuyaCloudError("group shadow unreadable")

        bright = _as_int(props.get(DP_BRIGHT)) if info.get("brightness") else None
        return {
            "switch_led": _as_bool(props.get(DP_SWITCH)),
            "bright_value": bright,
            "temp_value": _as_int(props.get(DP_TEMP)),
            "work_mode": props.get(DP_WORK_MODE),
            "supports_brightness": bool(info.get("brightness")),
        }

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        sem = asyncio.Semaphore(4)

        async def guarded(gid: str, info: dict):
            async with sem:
                try:
                    return gid, await self._read_group(gid, info)
                except TuyaCloudError as err:
                    errors.append(f"{gid}: {err}")
                    return gid, None
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

    # ---------- control (group level only) ----------
    async def async_set_states(self, group_id: str, states: dict[str, Any]) -> None:
        """Write properties to the *group*; the cloud drives the members.

        `states` uses plain group property names (switch_led / bright_value /
        temp_value / work_mode).  Anything the group does not declare is
        dropped with a warning instead of being sent to devices.
        """
        info = self.groups.get(group_id) or {}
        body: dict[str, Any] = {}

        for key, value in states.items():
            if key not in GROUP_WRITABLE:
                _LOGGER.warning("group %s: %r is not a group property, ignored",
                                group_id, key)
                continue
            if key == DP_BRIGHT:
                if not info.get("brightness"):
                    _LOGGER.warning(
                        "group %s (%s) has no bright_value property -- brightness "
                        "cannot be controlled; skipped",
                        group_id, info.get("name"),
                    )
                    continue
                body[DP_BRIGHT] = max(10, min(int(value), 1000))
                # brightness only takes effect in white mode
                body.setdefault(DP_WORK_MODE, "white")
            elif key == DP_TEMP:
                body[DP_TEMP] = max(0, min(int(value), 1000))
                body.setdefault(DP_WORK_MODE, "white")
            elif key == DP_SWITCH:
                body[DP_SWITCH] = bool(value)
            else:
                body[key] = value

        if not body:
            return

        await self.api.set_group_properties(group_id, body)
        _LOGGER.debug("group %s <- %s", group_id, body)

        # optimistic local update
        cur = self.data.get(group_id)
        if cur is not None:
            if DP_SWITCH in body:
                cur["switch_led"] = bool(body[DP_SWITCH])
            if DP_BRIGHT in body:
                cur["bright_value"] = int(body[DP_BRIGHT])
            if DP_TEMP in body:
                cur["temp_value"] = int(body[DP_TEMP])
            if DP_WORK_MODE in body:
                cur["work_mode"] = body[DP_WORK_MODE]
            self.async_set_updated_data(self.data)
