"""按钮类实体：按一下只发送一条命令（不读回、无状态）。"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .serial_api import Channel, SerialWriter

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """按 configuration.yaml 里的按钮数创建 button 实体。"""
    store = hass.data[DOMAIN][entry.entry_id]
    buttons: list[Channel] = store["buttons"]
    writer: SerialWriter = store["writer"]
    device: str = store["device"]

    entities = [
        ModbusOnewayButton(entry, writer, device, idx, ch)
        for idx, ch in enumerate(buttons)
    ]
    if entities:
        async_add_entities(entities, update_before_add=False)


class ModbusOnewayButton(ButtonEntity):
    """一个按钮：按下只发送一条命令。"""

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
        self._attr_name = channel.name
        self._attr_unique_id = f"{device}_btn_{index}_{channel.name}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device)},
            "name": entry.title or "modbus单向控制",
            "manufacturer": "智道物联",
            "model": "USB-RS232/485 单向控制",
        }

    async def async_press(self) -> None:
        """按下：发送配置的那一条命令。"""
        code = self._channel.on_code
        if not code:
            _LOGGER.warning("%s 未配置命令，忽略", self._channel.name)
            return
        try:
            await self._writer.write_bytes(code)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("发送按钮命令失败（%s）：%s", self._channel.name, err)
