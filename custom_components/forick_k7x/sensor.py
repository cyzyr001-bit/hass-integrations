"""sensor 平台：单个「按键」事件传感器（8 键合一）。

状态编码（主人要求）：
- 第 N 键单击 → (N*10 + 1)，如键1单击=11、键2单击=21
- 第 N 键双击 → (N*10 + 2)，如键1双击=12
- 第 N 键长按 → (N*10 + 3)，如键1长按=13
- 无操作 → "0"

按键是瞬态事件：面板按键时发 06 写帧（主动上报），coordinator 监听到后
写入 keys 并 2 秒后清零，本传感器把「当前非零的那个键」合成一个状态值展示。

文档事件码：01=单击 02=双击 03=3击 04=长按按下 05=长按松开。
"""
from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HubData, K7xCoordinator
from .const import DEFAULT_TITLE, DOMAIN, KEY_COUNT, KEY_EVENT_MAP, KEY_EVENT_NAMES

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    hub: HubData = hass.data[DOMAIN][entry.entry_id]
    entities = [ForickKeySensor(hub, hub.coordinators[uid], entry, uid)
                for uid in hub.unit_ids]
    async_add_entities(entities)


class ForickKeySensor(CoordinatorEntity[K7xCoordinator], SensorEntity):
    """按键事件传感器（8 键合一）。

    任意一个键被按下时，状态显示对应编码（如 31=键3单击）；无按键时显示 0。
    属性里额外给出具体按键号、事件名、原始事件码。
    """

    _attr_has_entity_name = True

    def __init__(self, hub: HubData, coordinator: K7xCoordinator,
                 entry: ConfigEntry, unit_id: int) -> None:
        super().__init__(coordinator)
        self._hub = hub
        self._unit_id = unit_id
        self._attr_unique_id = f"{entry.entry_id}_u{unit_id}_key"
        self._attr_name = "按键"
        self._attr_icon = "mdi:gesture-tap-button"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_u{unit_id}")},
            "name": f"{DEFAULT_TITLE} (ID：{unit_id})",
            "manufacturer": "智道物联",
            "model": "八键智能开关面板",
        }

    def _active_key(self) -> int | None:
        """返回当前处于非零状态的按键号（1~8）；无则 None。"""
        data = self.coordinator.data or {}
        keys = data.get("keys") or []
        for i in range(len(keys)):
            if i < KEY_COUNT and keys[i] != 0:
                return i + 1
        return None

    @property
    def native_value(self) -> str | None:
        key = self._active_key()
        if key is None:
            return "0"
        data = self.coordinator.data or {}
        keys = data.get("keys") or []
        raw = keys[key - 1]
        event = KEY_EVENT_MAP.get(raw)
        if event is None:
            return str(raw)          # 未识别的事件码，原样展示
        return str(key * 10 + event)

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        keys = data.get("keys") or []
        key = self._active_key()
        attrs = {"keys": [keys[i] if i < len(keys) else 0 for i in range(KEY_COUNT)]}
        if key is not None:
            raw = keys[key - 1]
            event = KEY_EVENT_MAP.get(raw)
            attrs["key"] = key
            attrs["event"] = KEY_EVENT_NAMES.get(event, f"未知({raw})")
            attrs["raw_code"] = raw
        return attrs
