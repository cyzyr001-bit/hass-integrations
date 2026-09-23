"""switch 平台：每个继电器一个开关实体。"""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HubData, TcpRelayCoordinator
from .const import DEFAULT_TITLE, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    hub: HubData = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for uid in hub.unit_ids:
        coordinator = hub.coordinators[uid]
        entities.extend(
            TcpRelaySwitch(hub, coordinator, entry, uid, channel)
            for channel in range(1, hub.channels + 1)
        )
    async_add_entities(entities)


class TcpRelaySwitch(CoordinatorEntity[TcpRelayCoordinator], SwitchEntity):
    """某从站第 N 路继电器（纯开关）。"""

    _attr_has_entity_name = True

    def __init__(self, hub: HubData, coordinator: TcpRelayCoordinator,
                 entry: ConfigEntry, unit_id: int, channel: int) -> None:
        super().__init__(coordinator)
        self._hub = hub
        self._unit_id = unit_id
        self._channel = channel
        self._attr_unique_id = f"{entry.entry_id}_u{unit_id}_ch{channel}"
        self._attr_name = f"第{channel}路"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_u{unit_id}")},
            "name": f"{DEFAULT_TITLE} (ID：{unit_id})",
            "manufacturer": "智道物联",
            "model": f"{hub.channels}路继电器模块",
        }

    @property
    def _raw_value(self) -> bool | None:
        data = self.coordinator.data or {}
        switches = data.get("switches") or []
        if len(switches) >= self._channel:
            return bool(switches[self._channel - 1])
        return None

    @property
    def _is_optimistic(self) -> bool:
        data = self.coordinator.data or {}
        return self._channel in (data.get("optimistic") or {})

    @property
    def is_on(self) -> bool | None:
        raw = self._raw_value
        if raw is None:
            return None
        return raw

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_write_channel(self._channel, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_write_channel(self._channel, False)
