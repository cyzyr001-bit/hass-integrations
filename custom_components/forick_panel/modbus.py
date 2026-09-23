"""Modbus RTU over TCP 客户端（Forick 三合一环境面板）。

传输实测（与 K7x 完全一致的标准 Modbus RTU 双向）：
- **请求与响应均为标准 Modbus RTU**：功能码 03 读保持寄存器 / 06 写单寄存器，
  带 CRC16。
- 从站地址 = 面板 ID（十六进制，如面板标 "23" = 0x23 = 十进制 35）。
- 支持块读（一次读 0x0001~0x000F 共 15 个寄存器），大幅减少轮询次数。
- 面板物理按键时经网转串广播 06 写帧，靠 BusMonitor 实时捕获（及时性）。
- 隧道（内网穿透）偶发超时：读失败自动重试，并允许用「新连接」兜底。

只依赖标准库。
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


class PanelError(Exception):
    """面板通讯/协议错误。"""


class ForickPanelClient:
    """一路 TCP 连接；读/写寄存器（标准 Modbus RTU）。"""

    def __init__(self, host: str, port: int = DEFAULT_PORT,
                 unit_id: int = 0x23, timeout: float = 3.0) -> None:
        self._host = host
        self._port = port
        self._unit_id = unit_id
        self._timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._rx = bytearray()

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
        _LOGGER.debug("已连接 %s:%s (unit_id=%d)", self._host, self._port, self._unit_id)

    async def _async_drain(self) -> None:
        """丢弃连接上残留字节。"""
        if self._reader is None:
            return
        self._rx.clear()
        try:
            while True:
                chunk = await asyncio.wait_for(self._reader.read(256), 0.12)
                if not chunk:
                    break
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, OSError):
            pass

    async def _async_read_exact(self, nbytes: int) -> bytes:
        """从 socket 精确读 nbytes 字节（Modbus RTU 帧长可计算）。"""
        assert self._reader is not None
        deadline = asyncio.get_running_loop().time() + self._timeout
        data = bytearray()
        while len(data) < nbytes:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise PanelError("等待响应超时")
            try:
                chunk = await asyncio.wait_for(self._reader.read(nbytes - len(data)), remaining)
            except asyncio.TimeoutError as err:
                raise PanelError("等待响应超时") from err
            if not chunk:
                raise PanelError("连接已被对端关闭")
            data.extend(chunk)
        return bytes(data)

    async def _async_transact(self, frame: bytes, expected_len: int) -> bytes:
        """发一帧 Modbus RTU，读回 expected_len 字节响应并校验 CRC。

        隧道偶发超时：尝试 3 次，失败后强制重连再试（新连接兜底）。
        """
        async with self._lock:
            last_err: Exception | None = None
            for attempt in range(1, 4):
                try:
                    if not self.connected:
                        await self._async_connect()
                    await self._async_drain()
                    self._writer.write(frame)
                    await self._writer.drain()
                    resp = await self._async_read_exact(expected_len)
                    self._check_response(resp)
                    return resp
                except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError,
                        PanelError) as err:
                    last_err = err
                    _LOGGER.debug("第 %s/%s 次请求失败(%s): %r",
                                  attempt, 3, frame.hex(" "), err)
                    await self.async_close()
                    if attempt < 3:
                        await asyncio.sleep(0.25)
            raise PanelError(
                f"通讯失败({type(last_err).__name__}): {last_err!r}") from last_err

    @staticmethod
    def _check_response(resp: bytes) -> None:
        """校验响应 CRC16；失败抛 PanelError。"""
        if len(resp) < 5:
            raise PanelError(f"响应过短: {resp.hex(' ')}")
        body, crc = resp[:-2], resp[-2:]
        if crc16(body) != crc:
            raise PanelError(f"响应 CRC 错误: {resp.hex(' ')}")
        # 异常响应（功能码高位 0x80）
        if body[1] & 0x80:
            code = body[2] if len(body) > 2 else 0
            raise PanelError(f"Modbus 异常响应 (code={code}): {resp.hex(' ')}")

    def _build_read(self, address: int, count: int = 1, unit_id: int | None = None) -> bytes:
        uid = unit_id if unit_id is not None else self._unit_id
        pdu = bytes([0x03, address >> 8, address & 0xFF, count >> 8, count & 0xFF])
        frame = bytes([uid]) + pdu
        return frame + crc16(frame)

    def _build_write(self, address: int, value: int, unit_id: int | None = None) -> bytes:
        uid = unit_id if unit_id is not None else self._unit_id
        pdu = bytes([0x06, address >> 8, address & 0xFF, value >> 8, value & 0xFF])
        frame = bytes([uid]) + pdu
        return frame + crc16(frame)

    async def read_register(self, address: int, unit_id: int | None = None) -> int:
        """功能码 0x03：读单个保持寄存器，返回整数值。"""
        frame = self._build_read(address, 1, unit_id)
        # 响应：addr(1) + func(1) + bytecount(1) + 2 bytes + crc(2) = 7
        resp = await self._async_transact(frame, 7)
        return int.from_bytes(resp[3:5], "big")

    async def read_registers(self, address: int, count: int,
                             unit_id: int | None = None) -> list[int]:
        """功能码 0x03：块读多个保持寄存器，返回整数值列表（顺序）。"""
        frame = self._build_read(address, count, unit_id)
        # 响应：addr + func + bytecount(=2*count) + 2*count bytes + crc
        resp_len = 5 + 2 * count
        resp = await self._async_transact(frame, resp_len)
        bytecount = resp[2]
        data = resp[3:3 + bytecount]
        return [int.from_bytes(data[i:i + 2], "big") for i in range(0, bytecount, 2)]

    async def write_register(self, address: int, value: int,
                             unit_id: int | None = None) -> None:
        """功能码 0x06：写单个保持寄存器（响应回显确认）。"""
        frame = self._build_write(address, value, unit_id)
        # 响应：addr(1) + func(1) + addr(2) + value(2) + crc(2) = 8
        await self._async_transact(frame, 8)


class BusMonitor:
    """独立 TCP 连接，被动监听网转串广播的帧。

    网转串是 TCP Server 广播模式：所有客户端都会收到总线上的每一帧。
    面板物理按键时发 06 写帧；监听器持续读这些广播帧，解析后通过回调
    实时推送给 coordinator（与八键开关的及时性方案一致）。
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
