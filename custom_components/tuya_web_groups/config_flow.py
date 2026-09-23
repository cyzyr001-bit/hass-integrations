"""Config flow for Tuya WebGroups.

Only a browser cookie is needed; the home and its groups are discovered
automatically.  The cookie is a session ticket, so we validate it live before
creating the entry and surface a clear error when it has expired.
"""
from __future__ import annotations

import logging

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

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
from .web_api import TuyaWebApi, TuyaWebError

_LOGGER = logging.getLogger(__name__)


class TuyaWebGroupsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Cookie in, all light groups out."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict = {}
        self._homes: list[dict] = []
        self._api: TuyaWebApi | None = None
        self._session: aiohttp.ClientSession | None = None

    async def async_step_user(self, user_input: dict | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            data = dict(user_input)
            session = aiohttp.ClientSession()
            api = TuyaWebApi(
                session, data[CONF_COOKIE],
                micro_app_id=data.get(CONF_MICRO_APP_ID) or DEFAULT_MICRO_APP_ID,
            )
            try:
                await api.ensure_session()
                homes = await api.homes()
                if not homes:
                    errors["base"] = "no_home"
                else:
                    self._data = data
                    self._homes = homes
                    self._api = api
                    self._session = session
                    home_id = str(data.get(CONF_HOME_ID) or "").strip()
                    if home_id:
                        await api.switch_home(home_id)
                        groups = await api.list_groups(home_id)
                        if not groups:
                            errors["base"] = "no_groups"
                        else:
                            return self._create_entry(home_id)
                    if not errors:
                        # ask which home to use
                        return await self.async_step_home()
            except TuyaWebError as err:
                _LOGGER.warning("web cookie validation failed: %s", err)
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("unexpected error validating cookie")
                errors["base"] = "unknown"
            finally:
                if errors and self._session is not None:
                    await self._session.close()
                    self._session = None
                    self._api = None

        schema = vol.Schema({
            vol.Required(CONF_COOKIE): str,
            vol.Optional(CONF_HOME_ID): str,
            vol.Optional(CONF_MICRO_APP_ID,
                         default=DEFAULT_MICRO_APP_ID): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    def _create_entry(self, home_id: str):
        title = next(
            (h.get("home_name") for h in self._homes
             if str(h.get("home_id")) == str(home_id)),
            None,
        ) or "Tuya WebGroups"
        data = dict(self._data)
        data[CONF_HOME_ID] = str(home_id)
        return self.async_create_entry(title=title, data=data)

    async def async_step_home(self, user_input: dict | None = None):
        """Let the user pick the home (accounts can own hundreds)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            home_id = str(user_input[CONF_HOME_ID])
            try:
                await self._api.switch_home(home_id)
                groups = await self._api.list_groups(home_id)
                if not groups:
                    errors["base"] = "no_groups"
                else:
                    return self._create_entry(home_id)
            except TuyaWebError as err:
                _LOGGER.warning("home %s groups failed: %s", home_id, err)
                errors["base"] = "unknown"

        # prefer the home the App last used
        default = next(
            (str(h.get("home_id")) for h in self._homes if h.get("is_last_chosen")),
            str(self._homes[0].get("home_id")) if self._homes else "",
        )
        options = {
            str(h.get("home_id")): f"{h.get('home_name')} ({h.get('home_id')})"
            for h in self._homes
        }
        schema = vol.Schema({
            vol.Required(CONF_HOME_ID, default=default): vol.In(options),
        })
        return self.async_show_form(step_id="home", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(entry: config_entries.ConfigEntry):
        return TuyaWebGroupsOptionsFlow(entry)


class TuyaWebGroupsOptionsFlow(config_entries.OptionsFlow):
    """Cookie refresh + optional filters."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input: dict | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            data = dict(user_input)
            data[CONF_GROUP_IDS] = [
                g.strip() for g in str(data.get(CONF_GROUP_IDS, "")).split(",")
                if g.strip()
            ]
            data[CONF_EXCLUDED_GROUPS] = [
                g.strip() for g in str(data.get(CONF_EXCLUDED_GROUPS, "")).split(",")
                if g.strip()
            ]
            data[CONF_SCAN_INTERVAL] = int(
                data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            )

            # validate the (possibly refreshed) cookie before saving
            session = aiohttp.ClientSession()
            api = TuyaWebApi(
                session, data.get(CONF_COOKIE) or self.entry.data.get(CONF_COOKIE, ""),
                micro_app_id=data.get(CONF_MICRO_APP_ID)
                or self.entry.options.get(CONF_MICRO_APP_ID)
                or DEFAULT_MICRO_APP_ID,
            )
            try:
                await api.ensure_session()
            except TuyaWebError:
                errors["base"] = "invalid_auth"
            finally:
                await session.close()

            if not errors:
                self.hass.config_entries.async_update_entry(self.entry, options=data)
                return self.async_create_entry(title="", data=data)

        cfg = {**self.entry.data, **self.entry.options}
        schema = vol.Schema({
            vol.Optional(CONF_COOKIE,
                         default=cfg.get(CONF_COOKIE, "")): str,
            vol.Optional(CONF_SCAN_INTERVAL,
                         default=cfg.get(CONF_SCAN_INTERVAL,
                                         DEFAULT_SCAN_INTERVAL)): int,
            vol.Optional(CONF_GROUP_NAMES,
                         default=cfg.get(CONF_GROUP_NAMES, "")): str,
            vol.Optional(CONF_GROUP_IDS,
                         default=",".join(cfg.get(CONF_GROUP_IDS) or [])): str,
            vol.Optional(CONF_EXCLUDED_GROUPS,
                         default=",".join(cfg.get(CONF_EXCLUDED_GROUPS) or [])): str,
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
