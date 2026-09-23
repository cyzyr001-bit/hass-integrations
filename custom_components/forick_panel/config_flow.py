"""配置流程：填写网转串（TCP 隧道）地址/端口 + 面板从站地址。"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback

from .const import (
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_ID,
    CONF_UNIT_IDS,
    CONF_UNIT_ID_LABELS,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TITLE,
    DEFAULT_UNIT_ID,
    DEFAULT_UNIT_ID_STR,
    DOMAIN,
    parse_unit_ids,
    parse_unit_id_labels,
)

STEP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
        vol.Optional(CONF_UNIT_ID, default=DEFAULT_UNIT_ID_STR): str,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=1440)
        ),
    }
)


def _format_ids(raw_ids) -> str:
    """把 unit_ids（可能是 int 列表或十六进制字符串）格式化成逗号分隔的
    十六进制字符串，用于 options 表单回显（如 [35,36] → "23,24"）。"""
    if isinstance(raw_ids, (list, tuple)):
        return ",".join(f"{u:X}" for u in raw_ids)
    # 字符串：可能是原始输入（如 "23,24"）或十进制，尽量保留原样
    text = str(raw_ids)
    # 尝试按十六进制理解并格式化
    try:
        parts = [p.strip() for p in text.replace("，", ",").split(",") if p.strip()]
        ints = []
        for p in parts:
            raw = p[2:] if p.lower().startswith("0x") else p
            ints.append(int(raw, 16))
        return ",".join(f"{u:X}" for u in ints)
    except ValueError:
        return text


class ForickPanelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
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
                await self.async_set_unique_id(
                    f"{host}:{port}:{','.join(map(str, unit_ids))}"
                )
                self._abort_if_unique_id_configured()
                data = {
                    CONF_HOST: host,
                    CONF_PORT: port,
                    CONF_UNIT_IDS: unit_ids,
                    CONF_UNIT_ID_LABELS: parse_unit_id_labels(user_input.get(CONF_UNIT_ID)),
                    CONF_SCAN_INTERVAL: user_input.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                }
                return self.async_create_entry(title=DEFAULT_TITLE, data=data)

        return self.async_show_form(
            step_id="user", data_schema=STEP_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: config_entries.ConfigEntry):
        return ForickPanelOptionsFlow()


class ForickPanelOptionsFlow(config_entries.OptionsFlow):
    """选项：IP/端口、面板从站地址、轮询间隔。"""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            ids = parse_unit_ids(user_input.get(CONF_UNIT_ID))
            data = dict(user_input)
            if ids:
                data[CONF_UNIT_IDS] = ids
                data[CONF_UNIT_ID_LABELS] = parse_unit_id_labels(user_input.get(CONF_UNIT_ID))
            return self.async_create_entry(data=data)
        entry = self.config_entry
        opts = {**entry.data, **entry.options}
        # 优先用原始 ID 标签（十六进制字符串，如 "23,24"）显示，
        # 避免把解析后的十进制（35,36）回显给用户造成困惑。
        labels = opts.get(CONF_UNIT_ID_LABELS)
        if labels:
            label_list = [l for l in labels if l]
            if label_list:
                default_ids = ",".join(label_list)
            else:
                raw_ids = opts.get(CONF_UNIT_IDS, opts.get(CONF_UNIT_ID, DEFAULT_UNIT_ID))
                default_ids = _format_ids(raw_ids)
        else:
            raw_ids = opts.get(CONF_UNIT_IDS, opts.get(CONF_UNIT_ID, DEFAULT_UNIT_ID))
            default_ids = _format_ids(raw_ids)
        schema = vol.Schema(
            {
                vol.Optional(CONF_HOST, default=opts.get(CONF_HOST, "")): str,
                vol.Optional(CONF_PORT, default=opts.get(CONF_PORT, DEFAULT_PORT)): vol.Coerce(int),
                vol.Optional(CONF_UNIT_ID, default=default_ids): str,
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
