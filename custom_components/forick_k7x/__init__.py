"""Forick（弗雷克）八键智能开关面板 — Home Assistant 集成入口。

设备：K7x 八键开关面板，Modbus RTU 从站，经 485 网转串以 TCP 透传接入（默认 8090）。
每个面板：4 路继电器 + 8 个按键 + 指示灯 + 红外（人体）感应。

实体：
- switch：4 路继电器 + 1 个指示灯总开关
- select：4 个「继电器 N 绑定」（选项：无 / 按键1~8）
- binary_sensor：红外感应（人体）
- sensor：8 个「按键 N」事件传感器（状态如 11=键1单击、12=键1双击、13=键1长按）

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
    CONF_SCAN_INTERVAL,
    CONF_UNIT_ID,
    CONF_UNIT_IDS,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UNIT_ID,
    DOMAIN,
    KEY_COUNT,
    KEY_EVENT_MAP,
    KEY_EVENT_RESET_SECONDS,
    IR_RESET_SECONDS,
    READ_BLOCK1_COUNT,
    READ_BLOCK1_START,
    READ_BLOCK2_COUNT,
    READ_BLOCK2_START,
    REG_INDICATOR,
    REG_IR_CONFIG,
    REG_IR_STATUS,
    REG_KEY_BASE,
    REG_RELAY_BIND,
    REG_RELAY_FEEDBACK,
    REG_RELAY_OUT,
    RELAY_COUNT,
    parse_unit_ids,
)
from .modbus import BusMonitor, ModbusError, ModbusRtuTcpClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SWITCH, Platform.SELECT, Platform.BINARY_SENSOR, Platform.SENSOR]


@dataclass
class HubData:
    """一个配置条目（一台网转串）的全部运行时对象。

    多个从站共享同一条 TCP 连接（client），但每个从站有独立的 coordinator。
    """

    client: ModbusRtuTcpClient
    monitor: BusMonitor
    coordinators: dict[int, "K7xCoordinator"]  # unit_id -> coordinator
    entry_id: str
    unit_ids: list[int]


class K7xCoordinator(DataUpdateCoordinator[dict]):
    """轮询某个从站的指示灯/按键/红外/继电器状态。

    分两块读（设备单次块读取上限约 17 个寄存器）：
    - 块1：50~66（指示灯 + 按键1~8 + 红外 + 配置 + 版本）
    - 块2：69~78（继电器绑定 + 输出）
    """

    def __init__(self, hass: HomeAssistant, client: ModbusRtuTcpClient,
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
        # 写入后的"乐观状态"：继电器 {n: (bool, 过期)}，指示灯 (bool, 过期) 或 None
        self._opt_relay: dict[int, tuple[bool, float]] = {}
        self._opt_indicator: tuple[bool, float] | None = None
        # 乐观值保持到「下一次轮询 + 缓冲」秒；轮询单位是分钟，故乘 60。
        # 期间若有总线广播帧（物理按键/面板主动上报）会用真实值覆盖。
        self._optimistic_ttl = float(scan_interval) * 60.0 + 30.0
        # 按键事件的"自动清零"定时器句柄：key_idx -> TimerHandle
        self._key_reset_handles: dict[int, object] = {}
        # 红外（人体感应）的"自动复位"定时器句柄
        self._ir_reset_handle: object | None = None

    @property
    def unit_id(self) -> int:
        return self._unit_id

    def set_optimistic_relay(self, relay: int, on: bool) -> None:
        self._opt_relay[relay] = (on, time.monotonic() + self._optimistic_ttl)

    def set_optimistic_indicator(self, on: bool) -> None:
        self._opt_indicator = (on, time.monotonic() + self._optimistic_ttl)

    # ---------------- 轮询 ----------------
    async def _async_update_data(self) -> dict:
        # 块1：指示灯 + 按键 + 红外 + 设备信息
        try:
            b1 = await self._client.async_read_holding(
                READ_BLOCK1_START, READ_BLOCK1_COUNT, unit_id=self._unit_id)
        except ModbusError as err:
            raise UpdateFailed(str(err)) from err
        # 块2：继电器绑定 + 输出
        try:
            b2 = await self._client.async_read_holding(
                READ_BLOCK2_START, READ_BLOCK2_COUNT, unit_id=self._unit_id)
        except ModbusError as err:
            raise UpdateFailed(str(err)) from err

        # 指示灯位图（寄存器 50 的低 8 位）
        indicator = b1[REG_INDICATOR - READ_BLOCK1_START] & 0xFF
        # 按键事件码（51~58 的低 8 位）；b1 从寄存器 50 起，故按键 n = b1[n]
        keys = [b1[i] & 0xFF for i in range(1, KEY_COUNT + 1)]
        # 红外状态（59）
        ir_status = b1[REG_IR_STATUS - READ_BLOCK1_START] & 0xFF
        # 红外配置（61）：0=关 1=仅唤醒 2=仅上报 3=唤醒并上报
        ir_config = b1[REG_IR_CONFIG - READ_BLOCK1_START] & 0xFF

        # 继电器输出 / 绑定
        relay_out: dict[int, bool] = {}
        relay_bind: dict[int, int] = {}
        for n in range(1, RELAY_COUNT + 1):
            out_reg = REG_RELAY_OUT[n] - READ_BLOCK2_START
            bind_reg = REG_RELAY_BIND[n] - READ_BLOCK2_START
            relay_out[n] = bool(b2[out_reg] & 1)
            relay_bind[n] = b2[bind_reg] & 0xFF
        # 继电器反馈（83）：0=不反馈 1=反馈
        relay_feedback = b2[REG_RELAY_FEEDBACK - READ_BLOCK2_START] & 0xFF

        # 刚写过的继电器，用乐观值覆盖
        now = time.monotonic()
        for n, (on, expires) in list(self._opt_relay.items()):
            if now > expires:
                self._opt_relay.pop(n, None)
                continue
            relay_out[n] = on
        # 指示灯乐观值
        if self._opt_indicator is not None:
            on, expires = self._opt_indicator
            if now > expires:
                self._opt_indicator = None
            else:
                indicator = 0xFF if on else 0x00

        return {
            "indicator": indicator,
            "keys": keys,
            "ir_status": ir_status,
            "ir_config": ir_config,
            "relay_out": relay_out,
            "relay_bind": relay_bind,
            "relay_feedback": relay_feedback,
            "opt_relay": dict(self._opt_relay),
            "opt_indicator": self._opt_indicator,
        }

    # ---------------- 写入 ----------------
    async def async_write_relay(self, relay: int, on: bool) -> None:
        """写第 relay 路（1~4）输出，写后不读总线，立即推乐观状态。"""
        self.set_optimistic_relay(relay, on)
        self._push_local()
        try:
            await self._client.async_write_register(
                REG_RELAY_OUT[relay], 1 if on else 0, unit_id=self._unit_id)
        except ModbusError as err:
            _LOGGER.warning("写从站 %s 继电器 %s 失败: %s（乐观状态保持，下个轮询周期纠正）",
                            self._unit_id, relay, err)

    async def async_write_indicator(self, on: bool) -> None:
        """指示灯总开关：on=全亮(0xFF)，off=全灭(0x00)。

        用功能码 06 写（带回帧确认），与反馈/绑定寄存器一致；实测 0x10 写
        指示灯不生效。注意：当继电器绑定按键后，指示灯跟随继电器状态，
        此时写指示灯寄存器会被设备忽略/联动覆盖，属正常硬件行为。
        """
        self.set_optimistic_indicator(on)
        self._push_local()
        try:
            await self._client.async_write_register_06(
                REG_INDICATOR, 0xFF if on else 0x00, unit_id=self._unit_id)
        except ModbusError as err:
            _LOGGER.warning("写从站 %s 指示灯失败: %s（乐观状态保持）",
                            self._unit_id, err)

    async def async_write_relay_bind(self, relay: int, key: int) -> None:
        """设置继电器 relay 绑定按键 key（0=无绑定，1~8=按键号）。

        面板绑定寄存器对 0x10 写 0（解除）不生效，故统一用功能码 06 写。
        写成功后**乐观更新本地缓存**：不立即 refresh（设备写 0 需要几百毫秒
        才生效，立即 refresh 会读回旧值导致 select 显示错误），下个轮询周期
        自动用真实硬件值纠正。
        """
        try:
            await self._client.async_write_register_06(
                REG_RELAY_BIND[relay], key, unit_id=self._unit_id)
        except ModbusError as err:
            _LOGGER.warning("写从站 %s 继电器 %s 绑定失败: %s",
                            self._unit_id, relay, err)
            return
        # 乐观更新：写成功（06 带回帧确认）即认为设备已接受，立即反映给 UI
        data = dict(self.data or {})
        relay_bind = dict(data.get("relay_bind") or {})
        relay_bind[relay] = key
        self.async_set_updated_data({**data, "relay_bind": relay_bind})

    async def async_write_relay_feedback(self, enabled: bool) -> None:
        """设置继电器反馈：True=反馈(01)，False=不反馈(00)。"""
        val = 1 if enabled else 0
        try:
            await self._client.async_write_register_06(
                REG_RELAY_FEEDBACK, val, unit_id=self._unit_id)
        except ModbusError as err:
            _LOGGER.warning("写从站 %s 继电器反馈失败: %s", self._unit_id, err)
            return
        data = dict(self.data or {})
        self.async_set_updated_data({**data, "relay_feedback": val})

    async def async_write_ir_config(self, value: int) -> None:
        """设置红外配置（0=关 1=仅唤醒 2=仅上报 3=唤醒并上报）。"""
        try:
            await self._client.async_write_register_06(
                REG_IR_CONFIG, value, unit_id=self._unit_id)
        except ModbusError as err:
            _LOGGER.warning("写从站 %s 红外配置失败: %s", self._unit_id, err)
            return
        data = dict(self.data or {})
        self.async_set_updated_data({**data, "ir_config": value})

    # ---------------- 总线广播帧 -------------
    def handle_bus_frame(self, addr: int, func: int, data: bytes) -> None:
        """处理网转串广播的帧（由 BusMonitor 回调，跑在事件循环内）。

        网转串广播总线上的每一帧。面板在以下情况会主动发 06 写帧：
        - 物理按键（写按键寄存器 51~58，值 0x06YY，YY=事件码）——瞬态事件
        - 按键绑定继电器后继电器动作（写继电器输出寄存器）
        - 面板自身状态变化
        这些帧不会出现在 03 轮询读回里（按键/红外是主动上报型），
        只能靠监听 06 写帧实时捕获。
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
        changed = False

        # 按键事件（寄存器 51~58，值 0x06YY 的低字节 YY = 事件码）
        if REG_KEY_BASE <= reg < REG_KEY_BASE + KEY_COUNT:
            key = reg - REG_KEY_BASE + 1      # 按键号 1~8
            keys = list(data.get("keys") or [0] * KEY_COUNT)
            keys[key - 1] = val & 0xFF
            data["keys"] = keys
            changed = True
            self._schedule_key_reset(key)

        # 红外（人体）感应（寄存器 59，面板主动上报）
        if reg == REG_IR_STATUS:
            data["ir_status"] = val & 0xFF
            changed = True
            if val & 0xFF:
                self._schedule_ir_reset()

        # 红外配置（寄存器 61）
        if reg == REG_IR_CONFIG:
            data["ir_config"] = val & 0xFF
            changed = True

        # 继电器反馈（寄存器 83）
        if reg == REG_RELAY_FEEDBACK:
            data["relay_feedback"] = val & 0xFF
            changed = True

        # 继电器输出
        for n in range(1, RELAY_COUNT + 1):
            if REG_RELAY_OUT[n] == reg:
                self._opt_relay.pop(n, None)   # 真实值优先，清掉乐观值
                relay_out = dict(data.get("relay_out") or {})
                relay_out[n] = bool(val & 1)
                data["relay_out"] = relay_out
                changed = True
                break

        # 指示灯
        if reg == REG_INDICATOR:
            self._opt_indicator = None
            data["indicator"] = val & 0xFF
            changed = True

        # 继电器绑定
        for n in range(1, RELAY_COUNT + 1):
            if REG_RELAY_BIND[n] == reg:
                relay_bind = dict(data.get("relay_bind") or {})
                relay_bind[n] = val & 0xFF
                data["relay_bind"] = relay_bind
                changed = True
                break

        if changed:
            self.async_set_updated_data(data)

    def _schedule_key_reset(self, key: int) -> None:
        """按键事件 N 秒后自动清零，保证每次按键都产生一次状态跳变。

        按键是瞬态事件：面板发 06 帧后就恢复，寄存器读回恒为 0x0600（低字节 0），
        轮询无法感知。所以监听帧时把事件写入 keys，然后定时清回 0，
        这样传感器状态呈现 0 → 11 → 0 的完整跳变，自动化可稳定触发。
        """
        def _clear() -> None:
            self._key_reset_handles.pop(key, None)
            data = dict(self.data or {})
            keys = list(data.get("keys") or [0] * KEY_COUNT)
            if key - 1 < len(keys) and keys[key - 1] != 0:
                keys[key - 1] = 0
                self.async_set_updated_data({**data, "keys": keys})

        old = self._key_reset_handles.pop(key, None)
        if old is not None:
            old.cancel()
        loop = self.hass.loop
        self._key_reset_handles[key] = loop.call_later(KEY_EVENT_RESET_SECONDS, _clear)

    def _schedule_ir_reset(self) -> None:
        """红外触发 N 秒后自动复位为未触发。

        面板红外触发时上报一次（写 59=1），之后可能不再主动发 0（取决于配置），
        故在监听到触发后定时把 ir_status 清回 0，让传感器呈现 on→off 的完整跳变。
        """
        def _clear() -> None:
            self._ir_reset_handle = None
            data = dict(self.data or {})
            if data.get("ir_status"):
                data["ir_status"] = 0
                self.async_set_updated_data(data)

        if self._ir_reset_handle is not None:
            self._ir_reset_handle.cancel()
        loop = self.hass.loop
        self._ir_reset_handle = loop.call_later(IR_RESET_SECONDS, _clear)

    def _push_local(self) -> None:
        """把当前乐观值合成进本地数据并推给实体，不触发任何总线读取。"""
        data = dict(self.data or {})
        relay_out = dict(data.get("relay_out") or {n: False for n in range(1, RELAY_COUNT + 1)})
        indicator = data.get("indicator", 0)
        for n, (on, _expires) in self._opt_relay.items():
            relay_out[n] = on
        if self._opt_indicator is not None:
            on, _expires = self._opt_indicator
            indicator = 0xFF if on else 0x00
        self.async_set_updated_data({
            **data,
            "indicator": indicator,
            "relay_out": relay_out,
            "opt_relay": dict(self._opt_relay),
            "opt_indicator": self._opt_indicator,
        })


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """建立连接、为每个从站启动轮询。"""
    opts = {**entry.data, **entry.options}
    host = opts[CONF_HOST]
    port = int(opts.get(CONF_PORT, DEFAULT_PORT))
    raw_ids = opts.get(CONF_UNIT_IDS, opts.get(CONF_UNIT_ID, DEFAULT_UNIT_ID))
    unit_ids = parse_unit_ids(raw_ids)
    if not unit_ids:
        unit_ids = [DEFAULT_UNIT_ID]
    scan_interval = int(opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

    client = ModbusRtuTcpClient(host, port, unit_ids[0])

    coordinators: dict[int, K7xCoordinator] = {}
    for uid in unit_ids:
        coordinator = K7xCoordinator(
            hass, client, entry.entry_id, uid, scan_interval)
        coordinators[uid] = coordinator

    # 逐个从站首次刷新；个别从站失败不阻断整体（记录警告）
    for uid, coordinator in coordinators.items():
        try:
            await coordinator.async_config_entry_first_refresh()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("从站 %s 首次读取失败: %s", uid, err)

    # 总线监听器：实时捕获物理按键触发的 06 写帧 + 面板主动上报
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
    """从设备 registry 的 identifiers 反解出从站 ID。

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
    """从设备页删除单个从站（多从站时只删该从站；删到最后一个则删整个条目）。"""
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
    _LOGGER.info("已删除从站 %s，剩余 %s", unit_id, remaining)
    await hass.config_entries.async_reload(config_entry.entry_id)
    return True
