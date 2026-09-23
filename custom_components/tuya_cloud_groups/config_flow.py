"""Config flow for Tuya Cloud Groups.

The user only supplies the cloud credentials; every light group of the account
is imported automatically. space_id / group_ids remain *optional* overrides for
power users.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

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
    REGION_ENDPOINTS,
)
from .tuya_api import TuyaCloudError, TuyaOpenApi

_LOGGER = logging.getLogger(__name__)


async def _count_groups(api: TuyaOpenApi, uid: str | None, space_id: str | None) -> int:
    """How many groups can we reach with these settings?"""
    if space_id:
        return len(await api.list_groups(str(space_id)))
    if uid:
        total = 0
        for home in await api.get_user_homes(str(uid)):
            hid = home.get("home_id")
            if hid:
                total += len(await api.list_groups(str(hid)))
        return total
    return 0


class TuyaCloudGroupsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Credentials in, all light groups out."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict | None = None):
        errors: dict[str, str] = {}

        # prefill from an existing localtuya entry if present
        prefill: dict[str, Any] = {}
        if not self._data:
            try:
                for entry in self.hass.config_entries.async_entries("localtuya"):
                    d = {**entry.data, **entry.options}
                    if d.get("client_id") and d.get("client_secret"):
                        prefill = {
                            CONF_CLIENT_ID: d.get("client_id", ""),
                            CONF_CLIENT_SECRET: d.get("client_secret", ""),
                            CONF_USER_ID: d.get("user_id", ""),
                            CONF_REGION: d.get("region", DEFAULT_REGION),
                        }
                        break
            except Exception:  # noqa: BLE001
                pass

        if user_input is not None:
            data = dict(user_input)
            data[CONF_GROUP_IDS] = [
                g.strip() for g in str(data.get(CONF_GROUP_IDS, "")).split(",") if g.strip()
            ]
            if not data.get(CONF_SPACE_ID):
                data[CONF_SPACE_ID] = str(data.get(CONF_HOME_ID) or "").strip()

            session = aiohttp.ClientSession()
            api = TuyaOpenApi(
                session,
                data[CONF_CLIENT_ID],
                data[CONF_CLIENT_SECRET],
                data.get(CONF_REGION, DEFAULT_REGION),
            )
            try:
                await api.ensure_token()
                uid = data.get(CONF_USER_ID)
                if not uid and not data.get(CONF_SPACE_ID) and not data[CONF_GROUP_IDS]:
                    # nothing to go by: ask the cloud who we are
                    for dev in (await api.get_project_devices(1)):
                        info = await api.get_device(dev.get("id"))
                        if info.get("uid"):
                            uid = str(info["uid"])
                            data[CONF_USER_ID] = uid
                            break
                count = await _count_groups(api, uid, data.get(CONF_SPACE_ID))
                if count == 0 and not data[CONF_GROUP_IDS]:
                    errors["base"] = "no_groups"
            except TuyaCloudError as err:
                _LOGGER.warning("validation failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("unexpected error validating credentials")
                errors["base"] = "unknown"
            finally:
                await session.close()

            if not errors:
                await self.async_set_unique_id(data[CONF_CLIENT_ID])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Tuya Cloud Groups", data=data
                )
            self._data = {}

        schema = vol.Schema({
            vol.Required(CONF_CLIENT_ID,
                         default=prefill.get(CONF_CLIENT_ID, "")): str,
            vol.Required(CONF_CLIENT_SECRET,
                         default=prefill.get(CONF_CLIENT_SECRET, "")): str,
            vol.Optional(CONF_USER_ID,
                         default=prefill.get(CONF_USER_ID, "")): str,
            vol.Optional(CONF_REGION,
                         default=prefill.get(CONF_REGION, DEFAULT_REGION)):
                vol.In(list(REGION_ENDPOINTS)),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(entry: config_entries.ConfigEntry):
        return TuyaCloudGroupsOptionsFlow(entry)


class TuyaCloudGroupsOptionsFlow(config_entries.OptionsFlow):
    """Optional fine-tuning. Defaults are correct for a single-home account."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input: dict | None = None):
        if user_input is not None:
            data = dict(user_input)
            data[CONF_GROUP_IDS] = [
                g.strip() for g in str(data.get(CONF_GROUP_IDS, "")).split(",") if g.strip()
            ]
            data[CONF_SCAN_INTERVAL] = int(data.get(CONF_SCAN_INTERVAL,
                                                   DEFAULT_SCAN_INTERVAL))
            data[CONF_EXCLUDED_GROUPS] = [
                g.strip() for g in str(data.get(CONF_EXCLUDED_GROUPS) or "").split(",")
                if g.strip()
            ]
            if not data.get(CONF_SPACE_ID):
                data[CONF_SPACE_ID] = ""
            self.hass.config_entries.async_update_entry(self.entry, options=data)
            return self.async_create_entry(title="", data=data)

        cfg = {**self.entry.data, **self.entry.options}
        schema = vol.Schema({
            vol.Optional(CONF_SCAN_INTERVAL,
                         default=cfg.get(CONF_SCAN_INTERVAL,
                                         DEFAULT_SCAN_INTERVAL)): int,
            vol.Optional(CONF_GROUP_NAMES,
                         default=cfg.get(CONF_GROUP_NAMES, "")): str,
            vol.Optional(CONF_SPACE_ID,
                         default=cfg.get(CONF_SPACE_ID, "")): str,
            vol.Optional(CONF_GROUP_IDS,
                         default=",".join(cfg.get(CONF_GROUP_IDS) or [])): str,
            vol.Optional(CONF_EXCLUDED_GROUPS,
                         default=",".join(cfg.get(CONF_EXCLUDED_GROUPS) or [])): str,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
