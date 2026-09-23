"""Tuya Cloud Groups — sync Tuya cloud light groups into Home Assistant.

Fill in Access ID / Access Secret + your home (space ID) and every light group
of that home is imported automatically, with real control.

Verified against the live Tuya Cloud API (cn region):
  GET  /v2.1/cloud/thing/group?page_no=1&page_size=N&space_id={space_id}
  GET  /v2.1/cloud/thing/group/{group_id}
  GET  /v2.1/cloud/thing/group/{group_id}/devices?page_no=1&page_size=N
  GET  /v1.0/devices/{device_id}                  -> single device (name/status)
  GET  /v2.1/cloud/thing/group/{gid}/properties   -> group property spec
  GET  /v2.0/cloud/thing/group/{gid}/properties   -> live values (has bright_value)

Control is group-level only, so the Tuya group shadow (= what the mobile App
shows) stays in sync.
"""
from __future__ import annotations

import logging

import aiohttp
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_EXCLUDED_GROUPS,
    CONF_GROUP_IDS,
    CONF_GROUP_NAMES,
    CONF_HOME_ID,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_SPACE_ID,
    CONF_USER_ID,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    GROUP_HAS_BRIGHT,
)
from .coordinator import TuyaGroupCoordinator
from .tuya_api import TuyaCloudError, TuyaOpenApi

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = ["light"]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


def _parse_name_map(raw) -> dict[str, str]:
    """'15913033=主卧调光组,15692166=主卧灯组' -> {id: name}."""
    if not raw:
        return {}
    if not isinstance(raw, str):
        return {str(k): str(v) for k, v in dict(raw).items()}
    out: dict[str, str] = {}
    for pair in raw.replace("\n", ",").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


async def _build_groups(api: TuyaOpenApi, cfg: dict) -> dict[str, dict]:
    """Resolve the set of groups to expose -> {group_id: {name, members}}.

    Priority:
      1. explicit group_ids  -> only those
      2. space_id / home_id  -> that single home
      3. user_id / auto      -> every home of the account, all groups
    """
    space_id = cfg.get(CONF_SPACE_ID) or cfg.get(CONF_HOME_ID)
    name_map = _parse_name_map(cfg.get(CONF_GROUP_NAMES))
    configured = [str(g).strip() for g in (cfg.get(CONF_GROUP_IDS) or []) if str(g).strip()]

    rows: list[dict] = []

    if configured:
        for gid in configured:
            try:
                info = await api.get_group(gid)
            except TuyaCloudError as err:
                _LOGGER.warning("group %s unreadable: %s", gid, err)
                info = {}
            rows.append({
                "group_id": gid,
                "group_name": info.get("group_name", ""),
                "space_id": info.get("space_id"),
            })
    else:
        space_ids: list[str] = []
        if space_id:
            space_ids = [str(space_id)]
        else:
            uid = cfg.get(CONF_USER_ID)
            if not uid:
                uid = await _resolve_uid(api, cfg)
            if not uid:
                raise TuyaCloudError("cannot determine user id; fill in either space_id or user_id")
            for home in await api.get_user_homes(str(uid)):
                hid = home.get("home_id")
                if hid:
                    space_ids.append(str(hid))
            _LOGGER.debug("account %s -> %d home(s)", uid, len(space_ids))

        for sid in space_ids:
            try:
                rows.extend(await api.list_groups(sid))
            except TuyaCloudError as err:
                _LOGGER.warning("listing groups of space %s failed: %s", sid, err)

    groups: dict[str, dict] = {}
    excluded = {str(g) for g in (cfg.get(CONF_EXCLUDED_GROUPS) or [])}
    for row in rows:
        gid = str(row.get("group_id") or "")
        if not gid or gid in excluded:
            continue
        cloud_name = (row.get("group_name") or "").strip()
        if cloud_name.lower() in ("properties", ""):
            cloud_name = ""
        groups[gid] = {
            "name": name_map.get(gid) or cloud_name or f"Tuya Group {gid}",
            "cloud_name": cloud_name,
            "space_id": str(row.get("space_id") or ""),
            "brightness": False,
        }

    # attach members + capability (a group built from mixed products can end up
    # without a `bright_value` property and then silently ignores亮度 writes)
    for gid, info in groups.items():
        try:
            members = await api.get_group_members(gid)
            info["members"] = [m.get("device_id") for m in members if m.get("device_id")]
        except TuyaCloudError as err:
            _LOGGER.warning("group %s members unreadable: %s", gid, err)
            info["members"] = []
        if not info["members"]:
            _LOGGER.warning("group %s has no member devices, skipping", gid)

        try:
            spec = await api.get_group_spec(gid)
            info["brightness"] = GROUP_HAS_BRIGHT in spec
            if not info["brightness"]:
                _LOGGER.warning(
                    "group %s (%s) has no %s property -- brightness will be "
                    "read-only/unavailable", gid, info.get("name"), GROUP_HAS_BRIGHT,
                )
        except TuyaCloudError as err:
            _LOGGER.warning("group %s spec unreadable: %s", gid, err)

    return {g: i for g, i in groups.items() if i.get("members")}


async def _resolve_uid(api: TuyaOpenApi, cfg: dict) -> str | None:
    """Find the account UID when the user did not supply one."""
    try:
        devices = await api.get_project_devices()
        if devices:
            dev = await api.get_device(devices[0].get("id"))
            if dev.get("uid"):
                return str(dev["uid"])
    except TuyaCloudError as err:
        _LOGGER.debug("uid resolution via device failed: %s", err)
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    cfg = {**entry.data, **entry.options}
    _LOGGER.debug(
        "setup: data=%s options=%s", sorted(entry.data), dict(entry.options)
    )

    session = aiohttp.ClientSession()
    api = TuyaOpenApi(
        session,
        cfg[CONF_CLIENT_ID],
        cfg[CONF_CLIENT_SECRET],
        cfg.get(CONF_REGION, DEFAULT_REGION),
    )
    try:
        await api.ensure_token()
    except TuyaCloudError as err:
        await session.close()
        _LOGGER.error("Tuya auth failed: %s", err)
        return False

    try:
        groups = await _build_groups(api, cfg)
    except TuyaCloudError as err:
        await session.close()
        _LOGGER.error("Tuya group discovery failed: %s", err)
        return False

    if not groups:
        await session.close()
        _LOGGER.error(
            "No Tuya groups found (space_id=%r, group_ids=%r)",
            cfg.get(CONF_SPACE_ID) or cfg.get(CONF_HOME_ID),
            cfg.get(CONF_GROUP_IDS),
        )
        return False

    _LOGGER.info("Tuya Cloud Groups: %d group(s): %s", len(groups),
                 {g: i["name"] for g, i in groups.items()})
    coordinator = TuyaGroupCoordinator(
        hass, api, groups,
        int(cfg.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "session": session,
        "coordinator": coordinator,
        "groups": groups,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry
) -> bool:
    """Remove one light group from this config entry.

    Called when the user picks "Remove device" in the device's ⋮ menu.  The
    group is remembered in the entry options so it does not come back on the
    next reload, and it is dropped from the running coordinator so the entity
    disappears immediately.
    """
    domain, value = next(
        (ident for ident in device_entry.identifiers if ident[0] == DOMAIN),
        (None, None),
    )
    if value is None:
        return False

    group_id = str(value)
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id) or {}

    # stop polling / controlling the removed group right away
    coordinator = data.get("coordinator")
    if coordinator is not None:
        coordinator.groups.pop(group_id, None)
        if coordinator.data is not None:
            coordinator.data.pop(group_id, None)
        coordinator.async_set_updated_data(coordinator.data or {})
    (data.get("groups") or {}).pop(group_id, None)

    current = {**entry.data, **entry.options}
    excluded = {str(g) for g in (current.get(CONF_EXCLUDED_GROUPS) or [])}
    if group_id not in excluded:
        excluded.add(group_id)
        hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, CONF_EXCLUDED_GROUPS: sorted(excluded)},
        )

    _LOGGER.info("Tuya Cloud Groups: removed group %s from entry", group_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data and data.get("session"):
            await data["session"].close()
    return unloaded
