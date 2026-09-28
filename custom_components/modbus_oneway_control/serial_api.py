"""串口 API：向 USB 转 RS232/485 串口写入控制代码。

只发送，不接收反馈（需求：无反馈，开→发开代码，关→发关代码）。
使用阻塞式 pyserial，写入放到 HA 的 executor 线程里执行，
避免额外依赖（pyserial 随 HA 核心一起提供）。

支持**串口自动匹配**：多台 USB 转串口时，/dev/ttyUSBn 编号会随插拔顺序漂移，
配置里可以写 /dev/serial/by-id/... 稳定名；若配置的路径暂时不存在，
会尝试按稳定名/唯一串口自动匹配，避免“换了口就打不开”。
"""
from __future__ import annotations

import asyncio
import glob
import logging
import os
from dataclasses import dataclass

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass
class Channel:
    """一路开关的配置。"""

    name: str
    on_code: bytes
    off_code: bytes


def resolve_port(configured: str) -> tuple[str, str | None]:
    """把配置的串口解析成当前真实可用的端口。

    返回 (端口, 说明)。配置路径存在则原样返回；否则尝试：
      1) 按 by-id 稳定名里的型号/UUID 片段匹配
      2) 若当前只有唯一一个串口，直接用（打日志提醒）
    """
    cfg = (configured or "").strip()
    if cfg and os.path.exists(cfg):
        return cfg, None

    by_ids = sorted(glob.glob("/dev/serial/by-id/*"))
    tty_usb = sorted(glob.glob("/dev/ttyUSB*"))

    base = os.path.basename(cfg)
    for p in by_ids:
        if base and (base in os.path.basename(p)):
            return p, f"配置 {cfg} 不存在，已按稳定名改用 {p}"

    if len(by_ids) == 1:
        return by_ids[0], f"配置 {cfg} 不存在，已改用唯一串口 {by_ids[0]}"
    if len(tty_usb) == 1:
        return tty_usb[0], f"配置 {cfg} 不存在，已改用唯一串口 {tty_usb[0]}"

    return cfg, (
        f"配置 {cfg} 不存在，且当前有多个串口 {tty_usb or by_ids}，"
        "无法自动确定；请在集成选项里填写 /dev/serial/by-id/... 稳定名"
    )


class SerialWriter:
    """管理一个串口的写入（阻塞式 pyserial + executor）。"""

    def __init__(self, hass: HomeAssistant, device: str, baud_rate: int) -> None:
        self._hass = hass
        self._device = device
        self._baud = baud_rate
        self._ser = None
        self._lock = asyncio.Lock()

    # ---- 内部：阻塞操作（在 executor 中运行）----
    def _open_sync(self):
        import serial

        if self._ser is not None and getattr(self._ser, "is_open", False):
            return self._ser

        port, note = resolve_port(self._device)
        if note:
            _LOGGER.warning("串口自动匹配：%s", note)
        self._ser = serial.Serial(
            port=port,
            baudrate=self._baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1,
            write_timeout=2,
        )
        _LOGGER.info("串口 %s @ %d 已打开（配置：%s）", port, self._baud, self._device)
        return self._ser

    def _write_sync(self, data: bytes) -> None:
        ser = self._open_sync()
        try:
            ser.write(data)
            ser.flush()
        except Exception:
            # 写入失败（如 I/O error / 设备被拔）→ 关掉后重开一次再试
            self._close_sync()
            ser = self._open_sync()
            ser.write(data)
            ser.flush()

    def _close_sync(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:  # noqa: BLE001
                pass
            self._ser = None

    # ---- 对外异步接口 ----
    async def connect(self) -> None:
        """尝试打开串口；失败抛异常由上层捕获（不会致命，发送时会重试）。"""
        async with self._lock:
            await self._hass.async_add_executor_job(self._open_sync)

    async def write_bytes(self, data: bytes) -> None:
        """写一段字节到串口（加锁，串行发送）。"""
        async with self._lock:
            await self._hass.async_add_executor_job(self._write_sync, bytes(data))
            _LOGGER.debug("已发送(%s): %s", self._device, bytes(data).hex(" "))

    async def close(self) -> None:
        async with self._lock:
            await self._hass.async_add_executor_job(self._close_sync)


def parse_code(text: str) -> bytes:
    """把配置里的代码解析成字节。

    支持：
      - "A0 01 01 A2"（空格分隔十六进制）
      - "A0,01,01,A2"（逗号分隔）
      - "A00101A2"（连续无分隔，偶数长度）
      - "0xA0 0x01 0x01 0xA2"（0x 前缀）
      - "PWR=ON\\r\\n"（含 \\r \\n \\xHH 转义的文本命令）
      - "AT+ON"（纯文本命令，按 UTF-8 发送）
    """
    if text is None:
        raise ValueError("代码不能为空")
    s = str(text).strip()
    if not s:
        raise ValueError("代码不能为空")

    # 含转义的文本命令
    if "\\r" in s or "\\n" in s or "\\t" in s or "\\x" in s:
        return _unescape(s)

    # 去 0x 前缀
    cleaned = s.replace("0x", "").replace("0X", "")

    # 分隔符模式（空格 / 逗号 / 分号）
    for sep in (" ", ",", ";"):
        cleaned = cleaned.replace(sep, " ")
    tokens = [t for t in cleaned.split() if t]
    if tokens and all(_is_hex(t) for t in tokens):
        out = bytearray()
        for t in tokens:
            if len(t) <= 2:
                out.append(int(t, 16))
            elif len(t) % 2 == 0:
                out.extend(bytes.fromhex(t))
            else:
                raise ValueError(f"十六进制片段长度无效: {t}")
        return bytes(out)

    # 连续无分隔十六进制
    if len(s) % 2 == 0 and _is_hex(s):
        return bytes.fromhex(s)

    # 纯文本命令
    return s.encode("utf-8")


def _is_hex(tok: str) -> bool:
    return bool(tok) and all(c in "0123456789abcdefABCDEF" for c in tok)


def _unescape(s: str) -> bytes:
    """处理含 \\r \\n \\xHH 转义的字符串（按 latin1 逐字节）。"""
    out = bytearray()
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt == "r":
                out.append(0x0D)
                i += 2
                continue
            if nxt == "n":
                out.append(0x0A)
                i += 2
                continue
            if nxt == "t":
                out.append(0x09)
                i += 2
                continue
            if nxt == "0":
                out.append(0x00)
                i += 2
                continue
            if nxt == "\\":
                out.append(0x5C)
                i += 2
                continue
            if nxt == "x" and i + 3 < n + 1:
                hx = s[i + 2 : i + 4]
                try:
                    out.append(int(hx, 16))
                    i += 4
                    continue
                except ValueError:
                    pass
        out.extend(c.encode("utf-8"))
        i += 1
    return bytes(out)
