"""配置流程：填写 USB 硬件标识符 + 波特率 + 显示名（每台 USB 加一次）。"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    BAUD_RATES,
    CONF_BAUD,
    CONF_DEVICE,
    CONF_NAME,
    DEFAULT_BAUD,
    DEFAULT_TITLE,
    DOMAIN,
)


def _default_name(device: str) -> str:
    """用设备路径的末段作为默认显示名，如 /dev/ttyUSB0 -> ttyUSB0。"""
    d = (device or "").strip()
    if not d:
        return DEFAULT_TITLE
    return d.rsplit("/", 1)[-1]


class ModbusOnewayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """UI 配置：每台 USB 设备建一条条目。"""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            device = str(user_input[CONF_DEVICE]).strip()
            if not device:
                errors[CONF_DEVICE] = "invalid_device"
            else:
                await self.async_set_unique_id(device)
                self._abort_if_unique_id_configured()
                name = str(user_input.get(CONF_NAME, "")).strip() or _default_name(device)
                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_DEVICE: device,
                        CONF_BAUD: user_input[CONF_BAUD],
                        CONF_NAME: name,
                    },
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE): str,
                vol.Optional(CONF_BAUD, default=DEFAULT_BAUD): vol.In(BAUD_RATES),
                vol.Optional(CONF_NAME, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: config_entries.ConfigEntry):
        """支持选项流：修改 USB 设备 / 波特率 / 显示名。"""
        return ModbusOnewayOptionsFlow()


class ModbusOnewayOptionsFlow(config_entries.OptionsFlow):
    """选项：修改 USB 设备路径、波特率、显示名。"""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        entry = self.config_entry
        if user_input is not None:
            data = dict(user_input)
            if not str(data.get(CONF_NAME, "")).strip():
                data[CONF_NAME] = _default_name(data.get(CONF_DEVICE, ""))
            return self.async_create_entry(data=data)

        data = {**entry.data, **entry.options}
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_DEVICE, default=data.get(CONF_DEVICE, "")
                ): str,
                vol.Optional(
                    CONF_BAUD, default=data.get(CONF_BAUD, DEFAULT_BAUD)
                ): vol.In(BAUD_RATES),
                vol.Optional(
                    CONF_NAME,
                    default=data.get(CONF_NAME, _default_name(data.get(CONF_DEVICE, ""))),
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
