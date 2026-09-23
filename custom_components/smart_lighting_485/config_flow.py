"""配置流程：填写网转串 IP/端口 + 从站地址（可多个，逗号分隔）。"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback

from .const import (
    CONF_BRIGHTNESS_MAX,
    CONF_CHANNELS,
    CONF_DIMMING,
    CONF_GAMMA,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_ID,
    CONF_UNIT_IDS,
    DEFAULT_BRIGHTNESS_MAX,
    DEFAULT_CHANNELS,
    DEFAULT_DIMMING,
    DEFAULT_GAMMA,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TITLE,
    DEFAULT_UNIT_ID,
    DOMAIN,
    parse_unit_ids,
)

STEP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
        vol.Optional(CONF_UNIT_ID, default=str(DEFAULT_UNIT_ID)): str,
        vol.Optional(CONF_CHANNELS, default=DEFAULT_CHANNELS): vol.Coerce(int),
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=300)
        ),
        vol.Optional(CONF_DIMMING, default=DEFAULT_DIMMING): bool,
        vol.Optional(CONF_BRIGHTNESS_MAX, default=DEFAULT_BRIGHTNESS_MAX): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
        vol.Optional(CONF_GAMMA, default=DEFAULT_GAMMA): vol.All(
            vol.Coerce(float), vol.Range(min=0.2, max=4.0)
        ),
    }
)


class SmartLighting485ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """UI 配置。"""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            unit_ids = parse_unit_ids(user_input.get(CONF_UNIT_ID))
            if not unit_ids:
                errors[CONF_UNIT_ID] = "invalid_unit_id"
            else:
                # 不验证设备是否在线，直接创建条目；不在线的从站后续显示为“离线”
                await self.async_set_unique_id(
                    f"{host}:{port}:{','.join(map(str, unit_ids))}"
                )
                self._abort_if_unique_id_configured()
                data = {
                    CONF_HOST: host,
                    CONF_PORT: port,
                    CONF_UNIT_IDS: unit_ids,
                    CONF_CHANNELS: user_input.get(CONF_CHANNELS, DEFAULT_CHANNELS),
                    CONF_SCAN_INTERVAL: user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    CONF_DIMMING: user_input.get(CONF_DIMMING, DEFAULT_DIMMING),
                    CONF_BRIGHTNESS_MAX: user_input.get(CONF_BRIGHTNESS_MAX, DEFAULT_BRIGHTNESS_MAX),
                    CONF_GAMMA: user_input.get(CONF_GAMMA, DEFAULT_GAMMA),
                }
                return self.async_create_entry(title=DEFAULT_TITLE, data=data)

        return self.async_show_form(
            step_id="user", data_schema=STEP_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: config_entries.ConfigEntry):
        """HA 2026.x 的 OptionsFlow 无 __init__；config_entry 是动态只读 property，
        由框架注入 handler 后解析。这里返回无参实例即可。"""
        return SmartLighting485OptionsFlow()


class SmartLighting485OptionsFlow(config_entries.OptionsFlow):
    """选项：IP/端口、设备 ID（可逗号分隔多从站）、路数、轮询间隔、调光模式。"""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            # 把逗号分隔的 ID 字符串解析成 unit_ids 列表存进 options
            ids = parse_unit_ids(user_input.get(CONF_UNIT_ID))
            data = dict(user_input)
            if ids:
                data[CONF_UNIT_IDS] = ids
            return self.async_create_entry(data=data)
        entry = self.config_entry
        opts = {**entry.data, **entry.options}
        raw_ids = opts.get(CONF_UNIT_IDS, opts.get(CONF_UNIT_ID, DEFAULT_UNIT_ID))
        if isinstance(raw_ids, (list, tuple)):
            default_ids = ",".join(map(str, raw_ids))
        else:
            default_ids = str(raw_ids)
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_HOST, default=opts.get(CONF_HOST, "")
                ): str,
                vol.Optional(
                    CONF_PORT, default=opts.get(CONF_PORT, DEFAULT_PORT)
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_UNIT_ID, default=default_ids
                ): str,
                vol.Optional(
                    CONF_CHANNELS, default=opts.get(CONF_CHANNELS, DEFAULT_CHANNELS)
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=300)),
                vol.Optional(
                    CONF_DIMMING, default=opts.get(CONF_DIMMING, DEFAULT_DIMMING)
                ): bool,
                vol.Optional(
                    CONF_BRIGHTNESS_MAX,
                    default=opts.get(CONF_BRIGHTNESS_MAX, DEFAULT_BRIGHTNESS_MAX),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Optional(
                    CONF_GAMMA, default=opts.get(CONF_GAMMA, DEFAULT_GAMMA)
                ): vol.All(vol.Coerce(float), vol.Range(min=0.2, max=4.0)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
