"""light 平台：每个可控硅通道一个可调光灯实体。"""
from __future__ import annotations

import logging
import math

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HubData, SmartLightingCoordinator
from .const import BITMAP_BIT_CH1, DEFAULT_TITLE, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    hub: HubData = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for uid in hub.unit_ids:
        coordinator = hub.coordinators[uid]
        entities.extend(
            SmartLightingChannel(hub, coordinator, entry, uid, channel)
            for channel in range(1, hub.channels + 1)
        )
    async_add_entities(entities)


class SmartLightingChannel(CoordinatorEntity[SmartLightingCoordinator], LightEntity):
    """某从站第 N 路（可控硅调光，0~brightness_max 对应 0~100% 亮度）。"""

    _attr_has_entity_name = True

    def __init__(self, hub: HubData, coordinator: SmartLightingCoordinator,
                 entry: ConfigEntry, unit_id: int, channel: int) -> None:
        super().__init__(coordinator)
        self._hub = hub
        self._unit_id = unit_id
        self._channel = channel
        # 每个从站一个独立设备，设备名统一为「标题 (ID：从站号)」
        self._attr_unique_id = f"{entry.entry_id}_u{unit_id}_ch{channel}"
        self._attr_name = f"第{channel}路"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_u{unit_id}")},
            "name": f"{DEFAULT_TITLE} (ID：{unit_id})",
            "manufacturer": "智道物联",
            "model": f"{hub.channels}路可控硅调光模块",
        }
        self._use_brightness = hub.dimming
        if self._use_brightness:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF

    # ---------------- 亮度曲线 ----------------
    @property
    def _device_max(self) -> int:
        return self._hub.brightness_max

    @property
    def _gamma(self) -> float:
        return self._hub.gamma

    def ha_to_device(self, bri_255: float) -> int:
        """HA 亮度(0~255) → 设备亮度(1~device_max)，带 gamma 曲线。"""
        ratio = max(0.0, min(1.0, bri_255 / 255.0))
        value = round(self._device_max * (ratio ** self._gamma))
        return max(1, min(self._device_max, value))

    def device_to_ha(self, raw: int) -> int:
        """设备亮度 → HA 亮度(1~255)，gamma 反算。"""
        ratio = max(0.0, min(1.0, raw / self._device_max))
        return max(1, min(255, round(255 * (ratio ** (1.0 / self._gamma)))))

    # ---------------- 状态 ----------------
    @property
    def _raw_value(self) -> int | None:
        data = self.coordinator.data or {}
        switches = data.get("switches") or []
        if len(switches) >= self._channel:
            return switches[self._channel - 1]
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
        # 刚由本集成写入：直接以写入值为准，避免被一次脏读弹回
        if self._is_optimistic:
            return raw > 0
        bitmap = (self.coordinator.data or {}).get("bitmap")
        if bitmap is not None:
            mask = 1 << (BITMAP_BIT_CH1 - (self._channel - 1))
            return bool(bitmap & mask)
        return raw != 0

    @property
    def brightness(self) -> int | None:
        if not self._use_brightness or not self.is_on:
            return None
        raw = self._raw_value
        if not raw:
            return None
        return self.device_to_ha(raw)

    # ---------------- 控制 ----------------
    async def async_turn_on(self, **kwargs) -> None:
        if self._use_brightness and ATTR_BRIGHTNESS in kwargs:
            value = self.ha_to_device(int(kwargs[ATTR_BRIGHTNESS]))
        elif self.is_on and self._raw_value:
            return                      # 已经是开着的，仅调整了的属性未变
        else:
            value = self._device_max
        await self.coordinator.async_write_channel(self._channel, value)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_write_channel(self._channel, 0)
