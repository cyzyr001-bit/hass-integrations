"""sensor 平台：面板室温传感器（随轮询刷新）。"""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HubData, PanelCoordinator
from .const import (
    DEVICE_NAME,
    DOMAIN,
    MANUFACTURER,
    MANUFACTURER_URL,
    MODEL,
    REG_ROOM_TEMP,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    hub: HubData = hass.data[DOMAIN][entry.entry_id]
    entities = [
        ForickRoomTemperature(hub, hub.coordinators[uid], entry, uid)
        for uid in hub.unit_ids
    ]
    async_add_entities(entities)


class ForickRoomTemperature(CoordinatorEntity[PanelCoordinator], SensorEntity):
    """面板室温传感器（值 = 寄存器 0x000D / 10）。"""

    _attr_has_entity_name = True
    _attr_name = "室温"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, hub: HubData, coordinator: PanelCoordinator,
                 entry: ConfigEntry, unit_id: int) -> None:
        super().__init__(coordinator)
        self._hub = hub
        self._entry = entry
        self._unit_id = unit_id
        label = hub.unit_id_labels.get(unit_id, f"{unit_id:X}")
        self._attr_unique_id = f"{entry.entry_id}_u{unit_id}_room_temp"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_u{unit_id}")},
            "name": f"{DEVICE_NAME} (ID：{label})",
            "manufacturer": MANUFACTURER,
            "model": MODEL,
            "configuration_url": MANUFACTURER_URL,
        }

    @property
    def native_value(self) -> float | None:
        raw = self.coordinator._read(REG_ROOM_TEMP) & 0xFFFF
        if 0x0000 <= raw <= 0x0258:
            return raw / 10.0
        if 0x0FEC <= raw <= 0x0FFF:
            return float(raw - 0x1000)
        return None
