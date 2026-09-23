"""Forick（弗雷克）三合一环境面板 — Home Assistant 集成入口。

设备：三合一环境面板（空调 + 地暖 + 新风），Modbus RTU 从站，经 485 网转串
以 TCP 透传接入。一个面板 = 1 个设备 = 3 个实体：
- climate：空调
- climate：地暖
- fan：新风

协议要点（实测）：
- 标准 Modbus RTU 双向：功能码 03 读 / 06 写单寄存器，带 CRC16。
- 从站地址 = 面板 ID（十六进制，面板标 "23" = 0x23 = 十进制 35）。
- 寄存器地址 1 起（PDF Dec 序号 = 实际地址）；支持块读 0x0001~0x000F。
- 一个网转串下可挂多个面板（逗号分隔 ID），每个面板一个独立设备。
- 面板物理按键时经总线广播 06 写帧，BusMonitor 实时捕获（及时性）。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_SCAN_INTERVAL,
    CONF_UNIT_ID,
    CONF_UNIT_ID_LABELS,
    CONF_UNIT_IDS,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TITLE,
    DEFAULT_UNIT_ID,
    DOMAIN,
    REG_AC_FAN,
    REG_AC_MODE,
    REG_AC_POWER,
    REG_AC_TEMP,
    REG_FA_FAN,
    REG_FA_POWER,
    REG_HEAT_POWER,
    REG_HEAT_TEMP,
    REG_ROOM_HUMID,
    REG_ROOM_TEMP,
    parse_unit_ids,
)
from .modbus import BusMonitor, ForickPanelClient, PanelError

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE, Platform.SENSOR]

# 块读范围：0x0001（空调开关）~ 0x000F（新风实时参数），一次 15 个寄存器。
_BLOCK_START = REG_AC_POWER
_BLOCK_COUNT = 15


@dataclass
class HubData:
    """一个配置条目（一台网转串）的全部运行时对象。

    多个面板共享同一条 TCP 连接（client），但每个面板有独立的 coordinator。
    """

    client: ForickPanelClient
    monitor: BusMonitor
    coordinators: dict[int, "PanelCoordinator"]  # unit_id -> coordinator
    entry_id: str
    unit_ids: list[int]
    unit_id_labels: dict[int, str]  # unit_id -> 原始十六进制标签（用于设备名显示）


class PanelCoordinator(DataUpdateCoordinator[dict]):
    """轮询某个面板的全部寄存器，合成 3 个实体需要的状态。"""

    def __init__(self, hass: HomeAssistant, client: ForickPanelClient,
                 entry_id: str, unit_id: int, scan_interval: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_u{unit_id}",
            update_interval=timedelta(minutes=scan_interval),
        )
        self._client = client
        self._entry_id = entry_id
        self._unit_id = unit_id
        # 写入后的"乐观状态"：寄存器地址 -> (值, 过期时刻)
        self._opt: dict[int, tuple[int, float]] = {}
        # 乐观值保持到下个轮询周期 + 缓冲
        self._optimistic_ttl = float(scan_interval) * 60.0 + 30.0

    @property
    def unit_id(self) -> int:
        return self._unit_id

    def set_optimistic(self, register: int, value: int) -> None:
        self._opt[register] = (value, time.monotonic() + self._optimistic_ttl)

    def _read(self, register: int) -> int:
        """优先返回乐观值，其次返回上次轮询缓存值，否则 0。"""
        opt = self._opt.get(register)
        if opt is not None:
            val, expires = opt
            if time.monotonic() > expires:
                self._opt.pop(register, None)
            else:
                return val
        data = self.data or {}
        return data.get("regs", {}).get(register, 0)

    async def _async_update_data(self) -> dict:
        # 一次块读 0x0001~0x000F 全部寄存器（含空调/地暖/新风/室温/湿度）
        try:
            values = await self._client.read_registers(
                _BLOCK_START, _BLOCK_COUNT, unit_id=self._unit_id)
        except PanelError as err:
            raise UpdateFailed(f"面板读取失败: {err}") from err

        regs: dict[int, int] = {}
        for idx, val in enumerate(values):
            regs[_BLOCK_START + idx] = val

        # 乐观值覆盖（刚写过的寄存器）
        now = time.monotonic()
        for reg, (val, expires) in list(self._opt.items()):
            if now > expires:
                self._opt.pop(reg, None)
                continue
            regs[reg] = val
        return {"regs": regs}

    # ---- 写入 ----
    async def async_write(self, register: int, value: int) -> None:
        """写单寄存器：先乐观更新，再写总线；失败回滚乐观值（真实值优先）。"""
        self.set_optimistic(register, value)
        self._push_local()
        try:
            await self._client.write_register(register, value, unit_id=self._unit_id)
        except PanelError as err:
            # 写失败（如面板写保护/异常响应）：回滚乐观值，恢复到真实缓存值
            _LOGGER.warning("写面板 %s 寄存器 0x%04X 失败: %s（回滚乐观值）",
                            self._unit_id, register, err)
            self._opt.pop(register, None)
            self._push_local()

    def _push_local(self) -> None:
        data = dict(self.data or {})
        regs = dict(data.get("regs") or {})
        now = time.monotonic()
        for reg, (val, expires) in self._opt.items():
            if now <= expires:
                regs[reg] = val
        self.async_set_updated_data({"regs": regs})

    # ---- 总线广播帧 ----
    def handle_bus_frame(self, addr: int, func: int, data: bytes) -> None:
        """处理网转串广播的帧（由 BusMonitor 回调，跑在事件循环内）。

        面板物理按键时发 06 写帧（写寄存器），靠此实时捕获并更新本地缓存，
        保证按面板后 HA 状态及时反馈（不等待下一轮轮询）。
        """
        if addr != self._unit_id:
            return
        if func == 0x06 and len(data) >= 4:
            reg = int.from_bytes(data[0:2], "big")
            val = int.from_bytes(data[2:4], "big")
            self._apply_register_update(reg, val)

    def _apply_register_update(self, reg: int, val: int) -> None:
        """把单个寄存器的广播更新合并进本地缓存。"""
        data = dict(self.data or {})
        regs = dict(data.get("regs") or {})
        # 真实值优先，清掉对应乐观值
        self._opt.pop(reg, None)
        regs[reg] = val
        self.async_set_updated_data({"regs": regs})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    opts = {**entry.data, **entry.options}
    host = opts[CONF_HOST]
    port = int(opts.get(CONF_PORT, DEFAULT_PORT))
    raw_ids = opts.get(CONF_UNIT_IDS, opts.get(CONF_UNIT_ID, DEFAULT_UNIT_ID))
    unit_ids = parse_unit_ids(raw_ids)
    if not unit_ids:
        unit_ids = [DEFAULT_UNIT_ID]
    # 原始 ID 标签（十六进制字符串，用于设备名显示）
    labels = opts.get(CONF_UNIT_ID_LABELS)
    if labels:
        label_list = [l for l in labels if l]
    else:
        label_list = [f"{u:X}" for u in unit_ids]
    unit_id_labels: dict[int, str] = {}
    for i, uid in enumerate(unit_ids):
        unit_id_labels[uid] = label_list[i] if i < len(label_list) else f"{uid:X}"
    scan_interval = int(opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

    client = ForickPanelClient(host, port, unit_ids[0])

    coordinators: dict[int, PanelCoordinator] = {}
    for uid in unit_ids:
        coordinator = PanelCoordinator(
            hass, client, entry.entry_id, uid, scan_interval)
        coordinators[uid] = coordinator

    # 逐个面板首次刷新；个别面板失败不阻断整体（记录警告）
    for uid, coordinator in coordinators.items():
        try:
            await coordinator.async_config_entry_first_refresh()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("面板 %s 首次读取失败: %s", uid, err)

    # 总线监听器：实时捕获面板物理按键触发的 06 写帧
    def _on_frame(addr: int, func: int, data: bytes) -> None:
        coordinator = coordinators.get(addr)
        if coordinator is not None:
            coordinator.handle_bus_frame(addr, func, data)

    monitor = BusMonitor(host, port, _on_frame)
    await monitor.start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = HubData(
        client=client,
        monitor=monitor,
        coordinators=coordinators,
        entry_id=entry.entry_id,
        unit_ids=unit_ids,
        unit_id_labels=unit_id_labels,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hub: HubData | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if hub is not None:
            await hub.monitor.stop()
            await hub.client.async_close()
    return unload_ok


# ---------------- 设备页「删除设备」----------------

def _unit_id_from_device(device_entry: dr.DeviceEntry) -> int | None:
    """从设备 registry 的 identifiers 反解出面板 ID。

    设备 identifier 格式为 `{entry_id}_u{unit_id}`。
    """
    for domain, identifier in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        marker = "_u"
        if marker in identifier:
            try:
                return int(identifier.rsplit(marker, 1)[-1])
            except ValueError:
                return None
    return None


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """从设备页删除单个面板（多面板时只删该面板；删到最后一个则删整个条目）。"""
    unit_id = _unit_id_from_device(device_entry)

    ent_reg = er.async_get(hass)
    dev_entities = [
        e for e in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)
        if e.device_id == device_entry.id
    ]

    if unit_id is None:
        if not dev_entities:
            _LOGGER.info("删除空壳设备 %s", device_entry.name)
            return True
        _LOGGER.warning("无法从设备标识符反解面板 ID，拒绝删除")
        return False

    opts = {**config_entry.data, **config_entry.options}
    raw_ids = opts.get(CONF_UNIT_IDS, opts.get(CONF_UNIT_ID, DEFAULT_UNIT_ID))
    unit_ids = parse_unit_ids(raw_ids)

    # 删除该面板关联的全部实体（unique_id 含 `_u{unit_id}_`）
    marker = f"_u{unit_id}_"
    stale = [
        ent.entity_id
        for ent in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)
        if marker in ent.unique_id
    ]
    for entity_id in stale:
        ent_reg.async_remove(entity_id)

    remaining = [u for u in unit_ids if u != unit_id]

    if not remaining:
        _LOGGER.info("删除最后一个面板 %s，移除整个配置条目", unit_id)
        await hass.config_entries.async_remove(config_entry.entry_id)
        return True

    new_data = dict(config_entry.data)
    new_options = dict(config_entry.options)
    if CONF_UNIT_IDS in new_data:
        new_data[CONF_UNIT_IDS] = remaining
    if CONF_UNIT_IDS in new_options:
        new_options[CONF_UNIT_IDS] = remaining
    if CONF_UNIT_IDS not in new_data and CONF_UNIT_IDS not in new_options:
        new_data[CONF_UNIT_IDS] = remaining
    hass.config_entries.async_update_entry(
        config_entry, data=new_data, options=new_options
    )
    _LOGGER.info("已删除面板 %s，剩余 %s", unit_id, remaining)
    await hass.config_entries.async_reload(config_entry.entry_id)
    return True
