"""Modbus RTU over TCP 客户端（TCP 继电器模块，RTU 透传，带 CRC）。

与调光模块同一类透明网转串设备：TCP 连上后设备先发版本 banner（如 "v1.0"），
随后用 Modbus RTU 帧（含 CRC16）通讯。继电器用线圈功能码 05（写）/ 01（读）。

设计要点（沿用调光模块踩坑后的成熟做法）：
- 累积缓冲 + 从流里重新找帧 + CRC 校验，错位/粘连/垃圾字节自动丢弃重同步。
- 每次请求前清空残留（顺带吃掉连接后的 banner）。
- 写线圈 fire-and-forget（不读回帧），状态靠轮询读回，避免写回帧与读回帧赛跑。
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
        """丢弃连接上残留的字节（含连接后设备主动发来的版本 banner）。"""
        if self._reader is None:
            return
        dropped = len(self._rx)
        self._rx.clear()
        try:
            while True:
                chunk = await asyncio.wait_for(self._reader.read(256), 0.15)
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

        共享连接下可能有其他从站的迟到响应，这里用当前请求的 uid 匹配，
        不匹配的帧直接丢弃，自动重新同步。
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
    async def read_coils(self, address: int, count: int = 1,
                         unit_id: int | None = None) -> list[bool]:
        """功能码 0x01：读线圈。返回 [bool, ...]，第 0 个对应 address 线圈。"""
        pdu = bytes([0x01, address >> 8, address & 0xFF, count >> 8, count & 0xFF])
        data = await self._async_request(pdu, unit_id)
        byte_count = (count + 7) // 8
        if len(data) < byte_count:
            raise ModbusError(f"读取长度异常: {len(data)} < {byte_count}")
        coils: list[bool] = []
        for i in range(count):
            byte = data[i // 8]
            coils.append(bool((byte >> (i % 8)) & 1))
        return coils

    async def write_coil(self, address: int, on: bool,
                         unit_id: int | None = None) -> None:
        """功能码 0x05：写单线圈，**不读回帧**（fire-and-forget）。

        写后不等待设备回帧，状态完全由轮询读回，写入结果用乐观状态立即反馈 UI。
        """
        value = 0xFF00 if on else 0x0000
        uid = unit_id if unit_id is not None else self._unit_id
        async with self._lock:
            last_err: Exception | None = None
            for attempt in range(1, 4):
                try:
                    if not self.connected:
                        await self._async_connect()
                    await self._async_drain()
                    frame = bytes([uid, 0x05, address >> 8, address & 0xFF,
                                   (value >> 8) & 0xFF, value & 0xFF])
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

    async def async_probe(self, unit_id: int | None = None) -> bool:
        """读 1 个线圈测试连通性，成功返回 True。"""
        await self.read_coils(0, 1, unit_id)
        return True
