"""climate 平台：空调 + 地暖 + 新风 三个实体。"""
from __future__ import annotations

import logging

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HubData, PanelCoordinator
from .const import (
    AC_MODE_COOL,
    AC_MODE_DRY,
    AC_MODE_FAN,
    AC_MODE_HEAT,
    DEVICE_NAME,
    DOMAIN,
    MANUFACTURER,
    MANUFACTURER_URL,
    MODEL,
    REG_AC_FAN,
    REG_AC_MODE,
    REG_AC_POWER,
    REG_AC_TEMP,
    REG_FA_FAN,
    REG_FA_POWER,
    REG_HEAT_POWER,
    REG_HEAT_TEMP,
    REG_ROOM_TEMP,
    TEMP_MAX,
    TEMP_MIN,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    hub: HubData = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for uid in hub.unit_ids:
        coordinator = hub.coordinators[uid]
        entities.append(ForickAirConditioner(hub, coordinator, entry, uid))
        entities.append(ForickFloorHeating(hub, coordinator, entry, uid))
        entities.append(ForickFreshAir(hub, coordinator, entry, uid))
    async_add_entities(entities)


def _device_info(hub: HubData, entry: ConfigEntry, unit_id: int) -> dict:
    label = hub.unit_id_labels.get(unit_id, f"{unit_id:X}")
    return {
        "identifiers": {(DOMAIN, f"{entry.entry_id}_u{unit_id}")},
        "name": f"{DEVICE_NAME} (ID：{label})",
        "manufacturer": MANUFACTURER,
        "model": MODEL,
        "configuration_url": MANUFACTURER_URL,
    }


class _BaseClimate(CoordinatorEntity[PanelCoordinator], ClimateEntity):
    """共享温度换算 / 设备信息。"""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = 1.0
    _attr_target_temperature_step = 1.0
    _attr_min_temp = TEMP_MIN
    _attr_max_temp = TEMP_MAX

    def __init__(self, hub: HubData, coordinator: PanelCoordinator,
                 entry: ConfigEntry, unit_id: int) -> None:
        super().__init__(coordinator)
        self._hub = hub
        self._entry = entry
        self._unit_id = unit_id

    @property
    def current_temperature(self) -> float | None:
        raw = self.coordinator._read(REG_ROOM_TEMP) & 0xFFFF
        # 实时温度编码（PDF）：0000H~0258H = 0~60.0℃（值/10）；
        # 0FECH~0FFFH = -20.0~-1.0℃（值 - 1000H，直接为℃）
        if 0x0000 <= raw <= 0x0258:
            return raw / 10.0
        if 0x0FEC <= raw <= 0x0FFF:
            return float(raw - 0x1000)
        return None


class ForickAirConditioner(_BaseClimate):
    """空调实体（支持 制冷/制热/除湿/送风 + 风速）。"""

    _attr_name = "空调"
    _attr_icon = "mdi:air-conditioner"

    _HVAC_MAP = {
        AC_MODE_COOL: HVACMode.COOL,
        AC_MODE_HEAT: HVACMode.HEAT,
        AC_MODE_DRY: HVACMode.DRY,
        AC_MODE_FAN: HVACMode.FAN_ONLY,
    }
    _HVAC_TO_REG = {v: k for k, v in _HVAC_MAP.items()}

    def __init__(self, hub: HubData, coordinator: PanelCoordinator,
                 entry: ConfigEntry, unit_id: int) -> None:
        super().__init__(hub, coordinator, entry, unit_id)
        self._attr_unique_id = f"{entry.entry_id}_u{unit_id}_ac"
        self._attr_device_info = _device_info(hub, entry, unit_id)
        self._attr_hvac_modes = [
            HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT,
            HVACMode.DRY, HVACMode.FAN_ONLY,
        ]
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )
        self._attr_fan_modes = ["低速", "中速", "高速"]

    @property
    def _power_reg(self) -> int:
        return REG_AC_POWER

    @property
    def _temp_reg(self) -> int:
        return REG_AC_TEMP

    @property
    def _is_on(self) -> bool:
        return self.coordinator._read(self._power_reg) != 0

    @property
    def target_temperature(self) -> float | None:
        return float(self.coordinator._read(self._temp_reg))

    @property
    def hvac_mode(self) -> HVACMode:
        if not self._is_on:
            return HVACMode.OFF
        mode = self.coordinator._read(REG_AC_MODE) & 0xFF
        return self._HVAC_MAP.get(mode, HVACMode.COOL)

    @property
    def hvac_action(self) -> HVACAction:
        # 开机时高 4 位反馈阀/继电器工作状态（1=开），此处简化为：
        # 开机且当前模式为送风 → FAN，否则按目标制冷/制热粗略反馈。
        if not self._is_on:
            return HVACAction.OFF
        mode = self.coordinator._read(REG_AC_MODE) & 0xFF
        if mode == AC_MODE_FAN:
            return HVACAction.FAN
        return HVACAction.COOLING if mode == AC_MODE_COOL else HVACAction.HEATING

    @property
    def fan_mode(self) -> str | None:
        fan = self.coordinator._read(REG_AC_FAN) & 0xFF
        return {1: "低速", 2: "中速", 3: "高速"}.get(fan)

    async def async_set_temperature(self, **kwargs) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        value = int(round(temp))
        value = max(TEMP_MIN, min(TEMP_MAX, value))
        await self.coordinator.async_write(REG_AC_TEMP, value)

    async def async_turn_on(self) -> None:
        await self.coordinator.async_write(REG_AC_POWER, 1)

    async def async_turn_off(self) -> None:
        await self.coordinator.async_write(REG_AC_POWER, 0)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_write(REG_AC_POWER, 0)
            return
        reg_mode = self._HVAC_TO_REG.get(hvac_mode)
        if reg_mode is None:
            return
        # 面板规则：空调关闭时，模式/风速寄存器写保护，必须先开机再写模式
        if not self._is_on:
            await self.coordinator.async_write(REG_AC_POWER, 1)
        await self.coordinator.async_write(REG_AC_MODE, reg_mode)
        await self.coordinator.async_write(REG_AC_POWER, 1)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        fan_map = {"低速": 1, "中速": 2, "高速": 3}
        value = fan_map.get(fan_mode)
        if value is not None:
            # 面板规则：空调关闭时风速写保护，先开机再写风速
            if not self._is_on:
                await self.coordinator.async_write(REG_AC_POWER, 1)
            await self.coordinator.async_write(REG_AC_FAN, value)


class ForickFloorHeating(_BaseClimate):
    """地暖实体（仅 开/关 + 温度设定，无模式/风速）。"""

    _attr_name = "地暖"
    _attr_icon = "mdi:radiator"

    def __init__(self, hub: HubData, coordinator: PanelCoordinator,
                 entry: ConfigEntry, unit_id: int) -> None:
        super().__init__(hub, coordinator, entry, unit_id)
        self._attr_unique_id = f"{entry.entry_id}_u{unit_id}_heat"
        self._attr_device_info = _device_info(hub, entry, unit_id)
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )

    @property
    def _power_reg(self) -> int:
        return REG_HEAT_POWER

    @property
    def _temp_reg(self) -> int:
        return REG_HEAT_TEMP

    @property
    def _is_on(self) -> bool:
        return self.coordinator._read(self._power_reg) != 0

    @property
    def target_temperature(self) -> float | None:
        return float(self.coordinator._read(self._temp_reg))

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self._is_on else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        return HVACAction.HEATING if self._is_on else HVACAction.OFF

    async def async_set_temperature(self, **kwargs) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        value = int(round(temp))
        value = max(TEMP_MIN, min(TEMP_MAX, value))
        await self.coordinator.async_write(REG_HEAT_TEMP, value)

    async def async_turn_on(self) -> None:
        await self.coordinator.async_write(REG_HEAT_POWER, 1)

    async def async_turn_off(self) -> None:
        await self.coordinator.async_write(REG_HEAT_POWER, 0)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.coordinator.async_write(
            REG_HEAT_POWER, 1 if hvac_mode == HVACMode.HEAT else 0)


class ForickFreshAir(_BaseClimate):
    """新风实体（climate：开/关 + 风速 低/中/高）。"""

    _attr_name = "新风"
    _attr_icon = "mdi:air-filter"

    def __init__(self, hub: HubData, coordinator: PanelCoordinator,
                 entry: ConfigEntry, unit_id: int) -> None:
        super().__init__(hub, coordinator, entry, unit_id)
        self._attr_unique_id = f"{entry.entry_id}_u{unit_id}_fa"
        self._attr_device_info = _device_info(hub, entry, unit_id)
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.FAN_ONLY]
        self._attr_supported_features = (
            ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )
        self._attr_fan_modes = ["低速", "中速", "高速"]
        # 新风无温度设定，去掉温度相关属性
        self._attr_min_temp = None
        self._attr_max_temp = None

    @property
    def _is_on(self) -> bool:
        return self.coordinator._read(REG_FA_POWER) != 0

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.FAN_ONLY if self._is_on else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        return HVACAction.FAN if self._is_on else HVACAction.OFF

    @property
    def fan_mode(self) -> str | None:
        fan = self.coordinator._read(REG_FA_FAN) & 0xFF
        return {1: "低速", 2: "中速", 3: "高速"}.get(fan)

    async def async_turn_on(self) -> None:
        await self.coordinator.async_write(REG_FA_POWER, 1)

    async def async_turn_off(self) -> None:
        await self.coordinator.async_write(REG_FA_POWER, 0)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.coordinator.async_write(
            REG_FA_POWER, 1 if hvac_mode == HVACMode.FAN_ONLY else 0)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        fan_map = {"低速": 1, "中速": 2, "高速": 3}
        value = fan_map.get(fan_mode)
        if value is not None:
            # 新风关闭时风速写保护，先开机再写风速
            if not self._is_on:
                await self.coordinator.async_write(REG_FA_POWER, 1)
            await self.coordinator.async_write(REG_FA_FAN, value)
