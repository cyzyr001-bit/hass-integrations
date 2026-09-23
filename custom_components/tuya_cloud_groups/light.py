"""Light platform: expose Tuya cloud light groups as HA lights.

All control is group-level (see coordinator.py), so the Tuya group shadow --
and with it the mobile App -- stays in sync.
"""
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
    GROUP_BRIGHT_MIN,
    GROUP_TEMP_MAX,
    GROUP_TEMP_MIN,
    MAX_KELVIN,
    MIN_KELVIN,
)
from .coordinator import TuyaGroupCoordinator
from .tuya_api import TuyaCloudError

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
    coordinator: TuyaGroupCoordinator = data["coordinator"]
    groups: dict[str, dict] = data["groups"]

    async_add_entities(
        TuyaCloudGroupLight(coordinator, gid, info)
        for gid, info in groups.items()
    )


class TuyaCloudGroupLight(CoordinatorEntity[TuyaGroupCoordinator], LightEntity):
    """A Tuya cloud device group rendered as a light."""

    _attr_has_entity_name = False

    def __init__(self, coordinator: TuyaGroupCoordinator, group_id: str,
                 info: dict) -> None:
        super().__init__(coordinator)
        self._group_id = group_id
        self._attr_name = info.get("name") or f"Tuya Group {group_id}"
        self._attr_unique_id = f"tuya_cloud_group_{group_id}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(group_id))},
            "name": self._attr_name,
            "manufacturer": "Tuya",
            "model": "Cloud Light Group",
        }
        self._supports_brightness = bool(info.get("brightness"))

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
            # group value 10..1000; the App shows it as value/10 percent
            return max(1, round(int(raw) / GROUP_BRIGHT_MAX * 255))
        except (TypeError, ValueError):
            return None

    @property
    def color_temp_kelvin(self) -> int | None:
        raw = self._props.get("temp_value")
        if raw is None:
            return None
        try:
            # group temp 0..1000 maps straight onto 2700..6500 K
            return map_range(int(raw), GROUP_TEMP_MIN, GROUP_TEMP_MAX,
                             MIN_KELVIN, MAX_KELVIN)
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self.coordinator.groups.get(self._group_id, {})
        props = self._props
        return {
            "tuya_group_id": self._group_id,
            "member_count": len(info.get("members") or []),
            "supports_brightness": self._supports_brightness,
            "work_mode": props.get("work_mode"),
        }

    # ---------- control (group level) ----------
    async def async_turn_on(self, **kwargs: Any) -> None:
        states: dict[str, Any] = {"switch_led": True}

        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is not None:
            # HA 0..255 == 0..100% -> group 10..1000
            value = round(int(brightness) / 255 * GROUP_BRIGHT_MAX)
            states["bright_value"] = max(GROUP_BRIGHT_MIN,
                                         min(value, GROUP_BRIGHT_MAX))

        kelvin = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        if kelvin is not None:
            states["temp_value"] = map_range(
                int(kelvin), MIN_KELVIN, MAX_KELVIN,
                GROUP_TEMP_MIN, GROUP_TEMP_MAX,
            )

        if brightness is not None or kelvin is not None:
            states["work_mode"] = "white"

        try:
            await self.coordinator.async_set_states(self._group_id, states)
        except TuyaCloudError as err:
            _LOGGER.error("Failed to turn on group %s: %s", self._group_id, err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.async_set_states(self._group_id,
                                                    {"switch_led": False})
        except TuyaCloudError as err:
            _LOGGER.error("Failed to turn off group %s: %s", self._group_id, err)
