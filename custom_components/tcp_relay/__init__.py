"""TCP 继电器模块 — Home Assistant 集成入口。

设备：Modbus RTU 从站（纯继电器，线圈控制），经 TCP 透传接入（默认端口 50000）。
支持 4/8/16 路（可配置，最多 48 路），用 switch 平台（纯开关，无调光）。
一个网转串下可挂多个从站（逗号分隔），每个从站一个独立设备。
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
    CONF_CHANNELS,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_ID,
    CONF_UNIT_IDS,
    COIL_BASE,
    DEFAULT_CHANNELS,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UNIT_ID,
    DOMAIN,
    parse_unit_ids,
)
from .modbus import ModbusError, ModbusRtuTcpClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SWITCH]


@dataclass
class HubData:
    """一个配置条目（一台网转串）的全部运行时对象。

    多个从站共享同一条 TCP 连接（client），但每个从站有独立的 coordinator。
    """

    client: ModbusRtuTcpClient
    coordinators: dict[int, "TcpRelayCoordinator"]  # unit_id -> coordinator
    entry_id: str
    unit_ids: list[int]
    channels: int


class TcpRelayCoordinator(DataUpdateCoordinator[dict]):
    """轮询某个从站的各路线圈状态。

    一次请求读回全部线圈（地址从 0 起），返回 bit 位图。
    共享连接下，每个从站用自己的 unit_id 去读，串行化由 client 内部锁保证。
    """

    def __init__(self, hass: HomeAssistant, client: ModbusRtuTcpClient,
                 entry_id: str, unit_id: int, channels: int, scan_interval: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_u{unit_id}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self._entry_id = entry_id
        self._unit_id = unit_id
        self._channels = channels
        # 写入后的"乐观状态"：{通道: (写入值, 过期时间)}
        self._optimistic: dict[int, tuple[bool, float]] = {}
        # 乐观值 TTL：覆盖到下一次轮询，避免写后轮询还没到就回退旧值
        self._optimistic_ttl = float(scan_interval) + 15.0

    @property
    def unit_id(self) -> int:
        return self._unit_id

    @property
    def channels(self) -> int:
        return self._channels

    def set_channels(self, channels: int) -> None:
        self._channels = channels

    def set_optimistic(self, channel: int, value: bool) -> None:
        """记录一次主动写入，短时间内以它为准，避免脏数据把状态弹回去。"""
        self._optimistic[channel] = (value, time.monotonic() + self._optimistic_ttl)

    # ---------------- 轮询 ----------------
    async def _async_update_data(self) -> dict:
        try:
            coils = await self._client.read_coils(
                COIL_BASE, self._channels, unit_id=self._unit_id)
        except ModbusError as err:
            raise UpdateFailed(str(err)) from err

        switches = [bool(c) for c in coils[: self._channels]]

        # 刚写过的通道，用乐观值覆盖
        now = time.monotonic()
        for channel, (value, expires) in list(self._optimistic.items()):
            if now > expires:
                self._optimistic.pop(channel, None)
                continue
            if 1 <= channel <= len(switches):
                switches[channel - 1] = value

        return {"switches": switches, "optimistic": dict(self._optimistic)}

    # ---------------- 写入 ----------------
    async def async_write_channel(self, channel: int, on: bool) -> None:
        """写本从站第 channel 路（1 起）线圈，写后不读总线，立即推乐观状态。"""
        self.set_optimistic(channel, on)
        self._push_local()
        try:
            await self._client.write_coil(
                COIL_BASE + channel - 1, on, unit_id=self._unit_id)
        except ModbusError as err:
            _LOGGER.warning("写从站 %s 第 %s 路失败: %s（乐观状态保持，下个轮询周期纠正）",
                            self._unit_id, channel, err)

    def _push_local(self) -> None:
        """把当前乐观值合成进本地数据并推给实体，不触发任何总线读取。"""
        data = dict(self.data or {})
        switches = list(data.get("switches") or [False] * self._channels)
        for channel, (value, _expires) in self._optimistic.items():
            if 1 <= channel <= len(switches):
                switches[channel - 1] = value
        self.async_set_updated_data({
            "switches": switches,
            "optimistic": dict(self._optimistic),
        })


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """建立连接、为每个从站启动轮询。"""
    opts = {**entry.data, **entry.options}
    host = opts[CONF_HOST]
    port = int(opts.get(CONF_PORT, DEFAULT_PORT))
    # 兼容旧字段 unit_id（单个 int）与新的 unit_ids（逗号分隔）
    raw_ids = opts.get(CONF_UNIT_IDS, opts.get(CONF_UNIT_ID, DEFAULT_UNIT_ID))
    unit_ids = parse_unit_ids(raw_ids)
    if not unit_ids:
        unit_ids = [DEFAULT_UNIT_ID]
    channels = int(opts.get(CONF_CHANNELS, DEFAULT_CHANNELS))
    scan_interval = int(opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

    client = ModbusRtuTcpClient(host, port, unit_ids[0])

    coordinators: dict[int, TcpRelayCoordinator] = {}
    for uid in unit_ids:
        coordinator = TcpRelayCoordinator(
            hass, client, entry.entry_id, uid, channels, scan_interval)
        coordinators[uid] = coordinator

    # 逐个从站首次刷新；个别从站失败不阻断整体（记录警告）
    for uid, coordinator in coordinators.items():
        try:
            await coordinator.async_config_entry_first_refresh()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("从站 %s 首次读取失败: %s", uid, err)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = HubData(
        client=client,
        coordinators=coordinators,
        entry_id=entry.entry_id,
        unit_ids=unit_ids,
        channels=channels,
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
            await hub.client.async_close()
    return unload_ok


# ---------------- 设备页「删除设备」----------------

def _unit_id_from_device(device_entry: dr.DeviceEntry) -> int | None:
    """从设备 registry 的 identifiers 反解出从站 ID。

    设备 identifier 格式为 `{entry_id}_u{unit_id}`。
    兼容升级前的旧格式 `{entry_id}`（无 _u，视为 entry 的第一个从站）。
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
        # 旧格式：无 _u，返回 None（由调用方决定是否用 entry 默认从站）
    return None


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """从设备页删除单个从站（多从站时只删该从站；删到最后一个则删整个条目）。

    升级前的空壳设备（identifier 无 `_u`）无实体，直接放行删除。
    """
    unit_id = _unit_id_from_device(device_entry)

    ent_reg = er.async_get(hass)
    dev_entities = er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)
    dev_entities = [e for e in dev_entities if e.device_id == device_entry.id]

    if unit_id is None:
        # 无法反解从站 ID（升级前的旧格式设备）。若无实体，放行删除空壳。
        if not dev_entities:
            _LOGGER.info("删除空壳设备 %s", device_entry.name)
            return True
        _LOGGER.warning("无法从设备标识符反解从站 ID，拒绝删除")
        return False

    opts = {**config_entry.data, **config_entry.options}
    raw_ids = opts.get(CONF_UNIT_IDS, opts.get(CONF_UNIT_ID, DEFAULT_UNIT_ID))
    unit_ids = parse_unit_ids(raw_ids)

    # 删除该从站关联的全部实体（unique_id 含 `_u{unit_id}_`）
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
        _LOGGER.info("删除最后一个从站 %s，移除整个配置条目", unit_id)
        await hass.config_entries.async_remove(config_entry.entry_id)
        return True

    # 更新配置中的从站列表（data 与 options 都必须传完整 dict，不能传 None）
    new_data = dict(config_entry.data)
    new_options = dict(config_entry.options)
    if CONF_UNIT_IDS in new_data:
        new_data[CONF_UNIT_IDS] = remaining
    if CONF_UNIT_IDS in new_options:
        new_options[CONF_UNIT_IDS] = remaining
    # 确保至少有一处存了 unit_ids（优先 data，与 setup 读取顺序一致）
    if CONF_UNIT_IDS not in new_data and CONF_UNIT_IDS not in new_options:
        new_data[CONF_UNIT_IDS] = remaining
    hass.config_entries.async_update_entry(
        config_entry, data=new_data, options=new_options
    )
    _LOGGER.info("已删除从站 %s，剩余 %s", unit_id, remaining)
    await hass.config_entries.async_reload(config_entry.entry_id)
    return True
