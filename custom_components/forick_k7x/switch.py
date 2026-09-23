"""switch 平台：4 路继电器（指示灯已改为 select 平台）。"""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HubData, K7xCoordinator
from .const import DEFAULT_TITLE, DOMAIN, RELAY_COUNT

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    hub: HubData = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for uid in hub.unit_ids:
        coordinator = hub.coordinators[uid]
        entities.extend(
            ForickRelaySwitch(hub, coordinator, entry, uid, n)
            for n in range(1, RELAY_COUNT + 1)
        )
    async_add_entities(entities)


class ForickRelaySwitch(CoordinatorEntity[K7xCoordinator], SwitchEntity):
    """第 N 路继电器（纯开关）。"""

    _attr_has_entity_name = True

    def __init__(self, hub: HubData, coordinator: K7xCoordinator,
                 entry: ConfigEntry, unit_id: int, relay: int) -> None:
        super().__init__(coordinator)
        self._hub = hub
        self._unit_id = unit_id
        self._relay = relay
        self._attr_unique_id = f"{entry.entry_id}_u{unit_id}_relay{relay}"
        self._attr_name = f"继电器{relay}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_u{unit_id}")},
            "name": f"{DEFAULT_TITLE} (ID：{unit_id})",
            "manufacturer": "智道物联",
            "model": "八键智能开关面板",
        }

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        relay_out = data.get("relay_out") or {}
        if self._relay in relay_out:
            return relay_out[self._relay]
        return None

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_write_relay(self._relay, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_write_relay(self._relay, False)
