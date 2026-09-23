"""binary_sensor 平台：红外（人体）感应。"""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HubData, K7xCoordinator
from .const import DEFAULT_TITLE, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    hub: HubData = hass.data[DOMAIN][entry.entry_id]
    entities = [
        ForickIRBinarySensor(hub, hub.coordinators[uid], entry, uid)
        for uid in hub.unit_ids
    ]
    async_add_entities(entities)


class ForickIRBinarySensor(CoordinatorEntity[K7xCoordinator], BinarySensorEntity):
    """红外（人体）感应：触发=on。"""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(self, hub: HubData, coordinator: K7xCoordinator,
                 entry: ConfigEntry, unit_id: int) -> None:
        super().__init__(coordinator)
        self._hub = hub
        self._unit_id = unit_id
        self._attr_unique_id = f"{entry.entry_id}_u{unit_id}_ir"
        self._attr_name = "人体感应"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_u{unit_id}")},
            "name": f"{DEFAULT_TITLE} (ID：{unit_id})",
            "manufacturer": "智道物联",
            "model": "八键智能开关面板",
        }

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        ir = data.get("ir_status")
        if ir is None:
            return None
        return ir != 0
