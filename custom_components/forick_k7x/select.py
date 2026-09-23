"""select 平台：继电器绑定 + 继电器反馈 + 指示灯 + 红外配置。"""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

from . import HubData, K7xCoordinator
from .const import DEFAULT_TITLE, DOMAIN, RELAY_COUNT

_LOGGER = logging.getLogger(__name__)

OPTIONS_BIND = ["无"] + [f"按键{i}" for i in range(1, 9)]
OPTIONS_FEEDBACK = ["不反馈", "反馈"]
OPTIONS_INDICATOR = ["关", "开"]
OPTIONS_IR_CONFIG = ["关", "仅唤醒", "仅上报", "唤醒并上报"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    hub: HubData = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for uid in hub.unit_ids:
        coordinator = hub.coordinators[uid]
        entities.extend(
            ForickRelayBindSelect(hub, coordinator, entry, uid, n)
            for n in range(1, RELAY_COUNT + 1)
        )
        entities.append(ForickRelayFeedbackSelect(hub, coordinator, entry, uid))
        entities.append(ForickIndicatorSelect(hub, coordinator, entry, uid))
        entities.append(ForickIRConfigSelect(hub, coordinator, entry, uid))
    async_add_entities(entities)


class _BaseSelect(CoordinatorEntity[K7xCoordinator], SelectEntity):
    """共享设备信息；全部 select 归入「配置」分类。"""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub: HubData, coordinator: K7xCoordinator,
                 entry: ConfigEntry, unit_id: int) -> None:
        super().__init__(coordinator)
        self._hub = hub
        self._unit_id = unit_id
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_u{unit_id}")},
            "name": f"{DEFAULT_TITLE} (ID：{unit_id})",
            "manufacturer": "智道物联",
            "model": "八键智能开关面板",
        }


class ForickRelayBindSelect(_BaseSelect):
    """继电器 N 绑定按键。"""

    def __init__(self, hub: HubData, coordinator: K7xCoordinator,
                 entry: ConfigEntry, unit_id: int, relay: int) -> None:
        super().__init__(hub, coordinator, entry, unit_id)
        self._relay = relay
        self._attr_unique_id = f"{entry.entry_id}_u{unit_id}_relay{relay}_bind"
        self._attr_name = f"继电器{relay}绑定"
        self._attr_options = OPTIONS_BIND

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data or {}
        relay_bind = data.get("relay_bind") or {}
        if self._relay not in relay_bind:
            return None
        key = relay_bind[self._relay]
        if not 0 <= key <= 8:
            return None
        return OPTIONS_BIND[key]

    async def async_select_option(self, option: str) -> None:
        key = OPTIONS_BIND.index(option) if option in OPTIONS_BIND else 0
        await self.coordinator.async_write_relay_bind(self._relay, key)


class ForickRelayFeedbackSelect(_BaseSelect):
    """继电器反馈：不反馈 / 反馈。"""

    def __init__(self, hub: HubData, coordinator: K7xCoordinator,
                 entry: ConfigEntry, unit_id: int) -> None:
        super().__init__(hub, coordinator, entry, unit_id)
        self._attr_unique_id = f"{entry.entry_id}_u{unit_id}_relay_feedback"
        self._attr_name = "继电器反馈"
        self._attr_options = OPTIONS_FEEDBACK
        self._attr_icon = "mdi:access-point"

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data or {}
        val = data.get("relay_feedback")
        if val is None:
            return None
        return OPTIONS_FEEDBACK[1] if val else OPTIONS_FEEDBACK[0]

    async def async_select_option(self, option: str) -> None:
        enabled = option == OPTIONS_FEEDBACK[1]
        await self.coordinator.async_write_relay_feedback(enabled)


class ForickIndicatorSelect(_BaseSelect):
    """指示灯：关（全灭）/ 开（全亮）。"""

    def __init__(self, hub: HubData, coordinator: K7xCoordinator,
                 entry: ConfigEntry, unit_id: int) -> None:
        super().__init__(hub, coordinator, entry, unit_id)
        self._attr_unique_id = f"{entry.entry_id}_u{unit_id}_indicator"
        self._attr_name = "指示灯"
        self._attr_options = OPTIONS_INDICATOR
        self._attr_icon = "mdi:lightbulb-group"

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data or {}
        indicator = data.get("indicator")
        if indicator is None:
            return None
        return OPTIONS_INDICATOR[1] if indicator != 0 else OPTIONS_INDICATOR[0]

    async def async_select_option(self, option: str) -> None:
        on = option == OPTIONS_INDICATOR[1]
        await self.coordinator.async_write_indicator(on)


class ForickIRConfigSelect(_BaseSelect):
    """红外配置：关 / 仅唤醒 / 仅上报 / 唤醒并上报。"""

    def __init__(self, hub: HubData, coordinator: K7xCoordinator,
                 entry: ConfigEntry, unit_id: int) -> None:
        super().__init__(hub, coordinator, entry, unit_id)
        self._attr_unique_id = f"{entry.entry_id}_u{unit_id}_ir_config"
        self._attr_name = "红外配置"
        self._attr_options = OPTIONS_IR_CONFIG
        self._attr_icon = "mdi:motion-sensor"

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data or {}
        val = data.get("ir_config")
        if val is None:
            return None
        if 0 <= val < len(OPTIONS_IR_CONFIG):
            return OPTIONS_IR_CONFIG[val]
        return None

    async def async_select_option(self, option: str) -> None:
        value = OPTIONS_IR_CONFIG.index(option) if option in OPTIONS_IR_CONFIG else 1
        await self.coordinator.async_write_ir_config(value)
