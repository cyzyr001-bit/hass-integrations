"""Modbus RTU over TCP 客户端（透过 485 网转串透传）。

只依赖标准库。

设计要点（针对透明传输网转串的实测问题）：
- 网转串会把 485 上的字节原样吐出来，多个 TCP 客户端 / 迟到的响应 / 面板主动上报
  会导致**帧错位、粘连、夹带垃圾字节**。因此不做"正好读 N 字节"，而是
  **累积缓冲 + 从流里重新找帧 + CRC 校验**，垃圾字节直接丢弃，自动重新同步。
- 每次请求前清空残留，失败重试 3 次。
- 面板单次块读取上限约 17 个寄存器，读更多会超时（由 coordinator 分块读）。
"""
from __future__ import annotations

import asyncio
import logging
import struct

from .const import DEFAULT_PORT

_LOGGER = logging.getLogger(__name__)


def crc16(data: bytes) -> bytes:
    """Modbus RTU CRC16（多项式 0xA001，初值 0xFFFF），小端返回。"""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


class ModbusError(Exception):
    """Modbus 通讯/协议错误。"""


class ModbusRtuTcpClient:
    """一路 TCP 连接，请求串行化；带帧重新同步能力。"""

    def __init__(self, host: str, port: int = DEFAULT_PORT,
                 unit_id: int = 1, timeout: float = 3.0) -> None:
        self._host = host
        self._port = port
        self._unit_id = unit_id
        self._timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._rx = bytearray()

    # ---------------- 连接管理 ----------------
    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def async_close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        self._reader = None
        self._writer = None
        self._rx.clear()

    async def _async_connect(self) -> None:
        await self.async_close()
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port), timeout=self._timeout
        )
        _LOGGER.debug("已连接 %s:%s", self._host, self._port)

    async def _async_drain(self) -> None:
        """丢弃连接上残留的字节（含面板主动上报的帧）。"""
        if self._reader is None:
            return
        dropped = len(self._rx)
        self._rx.clear()
        try:
            while True:
                chunk = await asyncio.wait_for(self._reader.read(256), 0.08)
                if not chunk:
                    break
                dropped += len(chunk)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, OSError):
            pass
        if dropped:
            _LOGGER.debug("丢弃残留字节 %s 个", dropped)

    # ---------------- 帧解析 ----------------
    def _expected_length(self, buf: bytearray) -> int | None:
        """根据功能码推算整帧长度；无法识别返回 None。"""
        func = buf[1]
        if func & 0x80:                       # 异常响应：addr func code crc(2)
            return 5
        if func in (1, 2, 3, 4):              # addr func bytes data crc(2)
            return 3 + buf[2] + 2
        if func in (5, 6, 15, 16):            # 回显类固定 8 字节
            return 8
        return None

    def _try_parse(self, uid: int) -> bytes | None:
        """从缓冲里尝试取出一帧有效数据（返回 data 段），否则 None。

        共享连接下可能有其他从站的迟到响应或面板主动上报，这里用当前请求的
        uid 做匹配，不匹配的帧直接丢弃，自动重新同步。
        """
        while len(self._rx) >= 5:
            if self._rx[0] != uid:
                dropped = self._rx[0]
                del self._rx[0]
                _LOGGER.debug("丢弃错位字节 0x%02X（期望从站 %s）", dropped, uid)
                continue
            need = self._expected_length(self._rx)
            if need is None:
                del self._rx[0]
                continue
            if len(self._rx) < need:
                return None
            frame = bytes(self._rx[:need])
            if crc16(frame[:-2]) != frame[-2:]:
                del self._rx[0]               # CRC 不过，往后挪一位重新找
                continue
            del self._rx[:need]
            if frame[1] & 0x80:
                raise ModbusError(f"从站异常码 0x{frame[2]:02X}")
            func = frame[1]
            if func in (1, 2, 3, 4):
                return frame[3:-2]
            return frame[2:-2]                # 回显类
        return None

    async def _async_read_response(self, uid: int) -> bytes:
        assert self._reader is not None
        deadline = asyncio.get_running_loop().time() + self._timeout
        while True:
            data = self._try_parse(uid)
            if data is not None:
                return data
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise ModbusError("等待响应超时")
            try:
                chunk = await asyncio.wait_for(self._reader.read(256), remaining)
            except asyncio.TimeoutError as err:
                raise ModbusError("等待响应超时") from err
            if not chunk:
                raise ModbusError("连接已被对端关闭")
            self._rx.extend(chunk)

    async def _async_request(self, pdu: bytes, unit_id: int | None = None) -> bytes:
        uid = unit_id if unit_id is not None else self._unit_id
        async with self._lock:
            last_err: Exception | None = None
            attempts = 3
            for attempt in range(1, attempts + 1):
                try:
                    if not self.connected:
                        await self._async_connect()
                    await self._async_drain()
                    frame = bytes([uid]) + pdu
                    frame += crc16(frame)
                    self._writer.write(frame)
                    await self._writer.drain()
                    return await self._async_read_response(uid)
                except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError,
                        ModbusError) as err:
                    last_err = err
                    _LOGGER.debug("第 %s/%s 次请求失败(%s): %r",
                                  attempt, attempts, pdu.hex(" "), err)
                    await self.async_close()
                    if attempt < attempts:
                        await asyncio.sleep(0.2)
            raise ModbusError(f"通讯失败({type(last_err).__name__}): {last_err!r}") from last_err

    # ---------------- 功能码 ----------------
    async def async_read_holding(self, address: int, count: int = 1,
                                 unit_id: int | None = None) -> list[int]:
        """功能码 0x03：读保持寄存器。"""
        pdu = bytes([0x03, address >> 8, address & 0xFF, count >> 8, count & 0xFF])
        data = await self._async_request(pdu, unit_id)
        if len(data) != count * 2:
            raise ModbusError(f"读取长度异常: {len(data)} != {count * 2}")
        return [int.from_bytes(data[i * 2:i * 2 + 2], "big") for i in range(count)]

    async def async_write_register(self, address: int, value: int,
                                   unit_id: int | None = None) -> None:
        """功能码 0x10：写单个寄存器，**不读回帧**（fire-and-forget）。

        面板对 0x10 写不回帧（实测写生效但无响应），故写后不等待回帧，
        避免写回帧与轮询读回帧在透明传输上赛跑错位；状态完全由轮询读回，
        写入结果用乐观状态立即反馈给 UI。
        """
        uid = unit_id if unit_id is not None else self._unit_id
        async with self._lock:
            last_err: Exception | None = None
            for attempt in range(1, 4):
                try:
                    if not self.connected:
                        await self._async_connect()
                    await self._async_drain()
                    frame = bytes([uid, 0x10, address >> 8, address & 0xFF,
                                   0x00, 0x01, 0x02, (value >> 8) & 0xFF, value & 0xFF])
                    frame += crc16(frame)
                    self._writer.write(frame)
                    await self._writer.drain()
                    return
                except (OSError, asyncio.IncompleteReadError) as err:
                    last_err = err
                    await self.async_close()
                    if attempt < 3:
                        await asyncio.sleep(0.2)
            raise ModbusError(f"写入失败({type(last_err).__name__}): {last_err!r}") from last_err

    async def async_write_register_06(self, address: int, value: int,
                                      unit_id: int | None = None) -> None:
        """功能码 0x06：写单个寄存器（带回帧确认）。

        面板的按键绑定寄存器对 0x10 写 0（解除）不生效，但 0x06 写 0 有效，
        故绑定类写入统一用 0x06。写失败会抛 ModbusError。
        """
        pdu = bytes([0x06, address >> 8, address & 0xFF,
                     value >> 8, value & 0xFF])
        await self._async_request(pdu, unit_id)

    async def async_write_registers(self, address: int, values: list[int],
                                    unit_id: int | None = None) -> None:
        """功能码 0x10：写多个寄存器。"""
        payload = b"".join(struct.pack(">H", v) for v in values)
        pdu = bytes([0x10, address >> 8, address & 0xFF,
                     (len(values) >> 8) & 0xFF, len(values) & 0xFF,
                     len(payload)]) + payload
        await self._async_request(pdu, unit_id)


class BusMonitor:
    """独立 TCP 连接，被动监听网转串广播的帧。

    网转串是 TCP Server 广播模式：所有客户端都会收到总线上的每一帧。
    面板物理按键绑定继电器时会发 06 写帧；按键/红外事件会主动上报 0x03 帧。
    监听器持续读这些广播帧，解析后通过回调实时推送给 coordinator。
    """

    def __init__(self, host: str, port: int, on_frame) -> None:
        self._host = host
        self._port = port
        self._on_frame = on_frame      # 同步回调 on_frame(addr, func, data: bytes)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._rx = bytearray()
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动后台监听循环。"""
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """停止监听并关闭连接。"""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        await self.async_close()

    async def async_close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        self._reader = None
        self._writer = None
        self._rx.clear()

    async def _run(self) -> None:
        while self._running:
            try:
                self._reader, self._writer = await asyncio.open_connection(
                    self._host, self._port)
                _LOGGER.debug("总线监听已连接 %s:%s", self._host, self._port)
                while self._running:
                    chunk = await self._reader.read(256)
                    if not chunk:
                        break
                    self._rx.extend(chunk)
                    self._parse_all()
            except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError):
                pass
            except asyncio.CancelledError:
                raise
            finally:
                await self.async_close()
            if self._running:
                await asyncio.sleep(2)   # 断开后重连间隔

    def _parse_all(self) -> None:
        while self._parse_one():
            pass

    def _parse_one(self) -> bool:
        """从缓冲里解析出一帧并回调；无完整帧返回 False。"""
        while len(self._rx) >= 5:
            func = self._rx[1]
            if func & 0x80:
                need = 5
            elif func in (1, 2, 3, 4):
                need = 3 + self._rx[2] + 2
            elif func in (5, 6):
                need = 8
            elif func in (15, 16):
                # 多写功能码：addr(1)+func(1)+起始(2)+数量(2)+字节数(1)+数据(N)+CRC(2)
                # N = buf[6]（数据字节数），整帧长 = 9 + N
                if len(self._rx) < 7:
                    return False
                need = 9 + self._rx[6]
            else:
                del self._rx[0]
                continue
            if len(self._rx) < need:
                return False
            frame = bytes(self._rx[:need])
            if crc16(frame[:-2]) != frame[-2:]:
                del self._rx[0]
                continue
            del self._rx[:need]
            addr = frame[0]
            f = frame[1]
            data = frame[2:-2]
            try:
                if self._on_frame is not None:
                    self._on_frame(addr, f, data)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("监听回调异常: %s", err)
            return True
        return False
