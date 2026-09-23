"""Tuya WebGroups — light groups via the Tuya Smart **web** (SaaS) API.

The web App is the only channel that can really drive brightness on groups
whose members come from different products: the developer OpenAPI
(`/v2.1/cloud/thing/group/...`) drops `bright_value` for such groups and
silently ignores writes to it.

Endpoints used (all under https://cn.device.tuyasmart.com)
----------------------------------------------------------
  GET  /api/smarthome/user/current                                -> who am I
  GET  /open-api/v2.0/m/sdf/smart-home/projects/sample            -> homes
  POST /api/smarthome/home/active                                 -> pick home
  GET  /open-api/v1.0/m/sdf/device-groups                         -> group list
  GET  /open-api/v1.0/m/sdf/device-groups/{gid}?product_id=...    -> live dps
  GET  /open-api/v1.0/m/sdf/device-groups/{gid}/functions?product_id=...
  GET  /open-api/v1.0/m/sdf/ss/panels/device/{home}/{dev}/model   -> abilityId
  POST /open-api/v1.0/m/sdf/device-groups/{gid}/control           -> control

Required headers: Cookie, csrf-token (window.csrf), uid, micro-app-id,
project-id (the active home -- without it the API answers 微应用权限错误).
"""
from __future__ import annotations

import logging

import aiohttp
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_COOKIE,
    CONF_EXCLUDED_GROUPS,
    CONF_GROUP_IDS,
    CONF_GROUP_NAMES,
    CONF_HOME_ID,
    CONF_MICRO_APP_ID,
    CONF_SCAN_INTERVAL,
    DEFAULT_MICRO_APP_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import TuyaWebGroupCoordinator
from .web_api import TuyaWebApi, TuyaWebError

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = ["light"]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


def _parse_name_map(raw) -> dict[str, str]:
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


async def _build_groups(api: TuyaWebApi, home_id: str,
                        cfg: dict) -> dict[str, dict]:
    """Resolve the groups to expose -> {group_id: {...}}.

    `home_id` is the active home (already selected), so the group list is
    already scoped to it.
    """
    rows = await api.list_groups(home_id)
    name_map = _parse_name_map(cfg.get(CONF_GROUP_NAMES))
    configured = [str(g).strip() for g in (cfg.get(CONF_GROUP_IDS) or [])
                  if str(g).strip()]
    excluded = {str(g) for g in (cfg.get(CONF_EXCLUDED_GROUPS) or [])}

    groups: dict[str, dict] = {}
    for row in rows:
        gid = str(row.get("group_id") or "")
        if not gid or gid in excluded:
            continue
        if configured and gid not in configured:
            continue
        cloud_name = (row.get("group_name") or "").strip()
        groups[gid] = {
            "name": name_map.get(gid) or cloud_name or f"Tuya Group {gid}",
            "cloud_name": cloud_name,
            "product_id": row.get("product_id"),
            "room_name": row.get("room_name"),
            "space_id": str(row.get("space_id") or ""),
            "members": [],
            "dps": {},
        }

    for gid, info in groups.items():
        pid = info.get("product_id")
        try:
            detail = await api.get_group(gid, pid)
        except TuyaWebError as err:
            _LOGGER.warning("group %s detail failed: %s", gid, err)
            detail = {}
        info["members"] = [d.get("device_id") for d in (detail.get("devices") or [])
                           if d.get("device_id")]
        # the group's own virtual device id is what the panel model needs
        master = detail.get("master_dev_id") or info.get("master_dev_id")
        if master:
            info["master_dev_id"] = master
            try:
                info["dps"] = await api.get_dp_numbers(home_id, master)
            except TuyaWebError as err:
                _LOGGER.debug("group %s dp map failed: %s", gid, err)

    return {g: i for g, i in groups.items()}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    cfg = {**entry.data, **entry.options}

    session = aiohttp.ClientSession()
    api = TuyaWebApi(session, cfg[CONF_COOKIE],
                     micro_app_id=cfg.get(CONF_MICRO_APP_ID, DEFAULT_MICRO_APP_ID))
    try:
        await api.ensure_session()
    except TuyaWebError as err:
        await session.close()
        _LOGGER.error("Tuya WebGroups auth failed: %s", err)
        return False

    home_id = str(cfg.get(CONF_HOME_ID) or "").strip()
    try:
        homes = await api.homes()
        if not home_id:
            home_id = str(homes[0].get("home_id")) if homes else ""
        if not home_id:
            raise TuyaWebError("no home found for this account")
        await api.switch_home(home_id)
        groups = await _build_groups(api, home_id, cfg)
    except TuyaWebError as err:
        await session.close()
        _LOGGER.error("Tuya WebGroups discovery failed: %s", err)
        return False

    if not groups:
        await session.close()
        _LOGGER.error("No Tuya web groups found (home=%s)", home_id)
        return False

    _LOGGER.info("Tuya WebGroups: %d group(s) in home %s: %s",
                 len(groups), home_id, {g: i["name"] for g, i in groups.items()})

    coordinator = TuyaWebGroupCoordinator(
        hass, api, groups, home_id,
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
    """Support removing one group from the ⋮ menu, persistently."""
    _, value = next(
        (ident for ident in device_entry.identifiers if ident[0] == DOMAIN),
        (None, None),
    )
    if value is None:
        return False

    group_id = str(value)
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id) or {}
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

    _LOGGER.info("Tuya WebGroups: removed group %s", group_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data and data.get("session"):
            await data["session"].close()
    return unloaded
