"""Light platform for Tuya WebGroups."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    GROUP_BRIGHT_MAX,
    GROUP_TEMP_MAX,
    GROUP_TEMP_MIN,
    MAX_KELVIN,
    MIN_KELVIN,
)
from .coordinator import TuyaWebGroupCoordinator
from .web_api import TuyaWebError

_LOGGER = logging.getLogger(__name__)


def map_range(value: float, from_min: float, from_max: float,
              to_min: float, to_max: float) -> int:
    if from_max == from_min:
        return int(to_min)
    scale = (to_max - to_min) / (from_max - from_min)
    mapped = to_min + (value - from_min) * scale
    return min(max(round(mapped), int(to_min)), int(to_max))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: TuyaWebGroupCoordinator = data["coordinator"]
    groups: dict[str, dict] = data["groups"]

    async_add_entities(
        TuyaWebGroupLight(coordinator, gid, info)
        for gid, info in groups.items()
    )


class TuyaWebGroupLight(CoordinatorEntity[TuyaWebGroupCoordinator], LightEntity):
    """A Tuya light group driven through the web (SaaS) API."""

    _attr_has_entity_name = False

    def __init__(self, coordinator: TuyaWebGroupCoordinator, group_id: str,
                 info: dict) -> None:
        super().__init__(coordinator)
        self._group_id = group_id
        self._attr_name = info.get("name") or f"Tuya Group {group_id}"
        self._attr_unique_id = f"tuya_web_group_{group_id}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(group_id))},
            "name": self._attr_name,
            "manufacturer": "Tuya",
            "model": "Web Light Group",
        }

    @property
    def _props(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get(self._group_id) or {}

    @staticmethod
    def _as_bool(v: Any) -> bool:
        if isinstance(v, bool):
            return v
        return str(v).lower() in ("true", "1", "yes", "on")

    # ---------- capabilities ----------
    @property
    def color_mode(self) -> ColorMode:
        return ColorMode.COLOR_TEMP

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        return {ColorMode.COLOR_TEMP}

    # ---------- state ----------
    @property
    def is_on(self) -> bool:
        return self._as_bool(self._props.get("switch_led"))

    @property
    def brightness(self) -> int | None:
        raw = self._props.get("bright_value")
        if raw is None:
            return None
        try:
            return max(1, round(int(raw) / GROUP_BRIGHT_MAX * 255))
        except (TypeError, ValueError):
            return None

    @property
    def color_temp_kelvin(self) -> int | None:
        raw = self._props.get("temp_value")
        if raw is None:
            return None
        try:
            return map_range(int(raw), GROUP_TEMP_MIN, GROUP_TEMP_MAX,
                             MIN_KELVIN, MAX_KELVIN)
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self.coordinator.groups.get(self._group_id, {})
        return {
            "tuya_group_id": self._group_id,
            "tuya_product_id": info.get("product_id"),
            "room": info.get("room_name"),
            "member_count": len(info.get("members") or []),
            "dp_numbers": info.get("dps"),
            "work_mode": self._props.get("work_mode"),
            "channel": "web",
        }

    # ---------- control ----------
    async def async_turn_on(self, **kwargs: Any) -> None:
        states: dict[str, Any] = {"switch_led": True}

        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is not None:
            value = round(int(brightness) / 255 * GROUP_BRIGHT_MAX)
            states["bright_value"] = max(10, min(value, GROUP_BRIGHT_MAX))

        kelvin = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        if kelvin is not None:
            states["temp_value"] = map_range(
                int(kelvin), MIN_KELVIN, MAX_KELVIN,
                GROUP_TEMP_MIN, GROUP_TEMP_MAX,
            )

        try:
            await self.coordinator.async_set_states(self._group_id, states)
        except TuyaWebError as err:
            _LOGGER.error("turn_on group %s failed: %s", self._group_id, err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.async_set_states(self._group_id,
                                                    {"switch_led": False})
        except TuyaWebError as err:
            _LOGGER.error("turn_off group %s failed: %s", self._group_id, err)
