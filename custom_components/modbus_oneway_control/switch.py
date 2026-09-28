"""开关类实体：每一路对应一个 switch，开/关时发送对应代码。

不读回设备状态（无反馈），但开关本身是**正常开关**：
本机记录开/关状态并在 HA 重启后恢复，前端显示为普通单键开关。
"""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .serial_api import Channel, SerialWriter

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """按 configuration.yaml 里的路数创建开关实体。"""
    store = hass.data[DOMAIN][entry.entry_id]
    channels: list[Channel] = store["channels"]
    writer: SerialWriter = store["writer"]
    device: str = store["device"]

    entities = [
        ModbusOnewaySwitch(entry, writer, device, idx, ch)
        for idx, ch in enumerate(channels)
    ]
    if entities:
        async_add_entities(entities, update_before_add=False)


class ModbusOnewaySwitch(SwitchEntity, RestoreEntity):
    """一路开关。

    - 只发送，不读回设备状态（无反馈）。
    - 状态为本机记录并在 HA 重启后恢复（RestoreEntity）。
    - 不是 assumed_state：前端显示为普通单键开关，可正常开/关。
    """

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        entry: ConfigEntry,
        writer: SerialWriter,
        device: str,
        index: int,
        channel: Channel,
    ) -> None:
        self._writer = writer
        self._device = device
        self._channel = channel
        self._is_on = False
        self._attr_name = channel.name
        self._attr_unique_id = f"{entry.entry_id}_sw_{index}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title or "modbus单向控制",
            "manufacturer": "智道物联",
            "model": "USB-RS232/485 单向控制",
        }

    async def async_added_to_hass(self) -> None:
        """恢复上次状态（重启后保持开关状态）。"""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._is_on = last.state == "on"

    @property
    def is_on(self) -> bool:
        return self._is_on

    async def async_turn_on(self, **kwargs) -> None:
        """发送开代码。"""
        code = self._channel.on_code
        if not code:
            _LOGGER.warning("%s 未配置开代码，忽略", self._channel.name)
            return
        try:
            await self._writer.write_bytes(code)
            self._is_on = True
            self.async_write_ha_state()
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("发送开代码失败（%s）：%s", self._channel.name, err)

    async def async_turn_off(self, **kwargs) -> None:
        """发送关代码。"""
        code = self._channel.off_code
        if not code:
            _LOGGER.warning("%s 未配置关代码，忽略", self._channel.name)
            return
        try:
            await self._writer.write_bytes(code)
            self._is_on = False
            self.async_write_ha_state()
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("发送关代码失败（%s）：%s", self._channel.name, err)
