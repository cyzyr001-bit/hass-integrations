"""TCP 客户端传输：把命令通过 TCP 直连发送（USB 转串口网关 / 网转串设备）。

支持配置里写 ``tcp://host:port``（如 tcp://192.168.110.158:6677）。

两种模式（可在集成选项里切换）：
- **长连接（persistent=True，默认）**：与服务端保持一条常连，
  后台任务持续监听对端是否断开（at_eof），断开自动重连；
  并开启 TCP keepalive 探测半开连接。发送前会确保连接可用。
- **发完即关（persistent=False）**：每次发送新建连接、发完立即关闭。

两种模式都保证"不会静默丢包"：长连接靠后台监听 + 发送前检查 + 失败重连，
短连接靠每次新建。
"""
from __future__ import annotations

import asyncio
import logging
import socket

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

TCP_PREFIX = "tcp://"


def is_tcp(target: str | None) -> bool:
    """判断目标是否为 TCP 地址（tcp://host:port）。"""
    return bool(target) and str(target).strip().lower().startswith(TCP_PREFIX)


def parse_tcp(target: str) -> tuple[str, int]:
    """解析 tcp://host:port → (host, port)。"""
    s = str(target).strip()
    if not s.lower().startswith(TCP_PREFIX):
        raise ValueError(f"不是 TCP 地址：{target}")
    body = s[len(TCP_PREFIX):]
    body = body.split("/", 1)[0]  # 兼容 tcp://host:port/xxx
    if ":" not in body:
        raise ValueError(f"TCP 地址缺少端口：{target}")
    host, port_s = body.rsplit(":", 1)
    host = host.strip()
    if not host:
        raise ValueError(f"TCP 地址缺少主机：{target}")
    port = int(port_s)
    if not (1 <= port <= 65535):
        raise ValueError(f"TCP 端口无效：{port}")
    return host, port


class TcpWriter:
    """TCP 客户端写入器。

    长连接模式下自带后台 reader 监控 + 断线自动重连 + keepalive。
    """

    def __init__(
        self,
        hass: HomeAssistant,
        target: str,
        persistent: bool = True,
    ) -> None:
        self._hass = hass
        self._target = target
        self._host, self._port = parse_tcp(target)
        self._persistent = bool(persistent)
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._closing = False

    @property
    def target(self) -> str:
        return self._target

    @property
    def persistent(self) -> bool:
        return self._persistent

    # ---------- 内部 ----------
    def _apply_keepalive(self, writer: asyncio.StreamWriter) -> None:
        """对底层 socket 开启 TCP keepalive（探测半开连接）。"""
        try:
            sock = writer.get_extra_info("socket")
            if sock is None:
                return
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            for opt, val in (
                ("TCP_KEEPIDLE", 30),    # 空闲 30s 开始探测
                ("TCP_KEEPINTVL", 10),   # 探测间隔 10s
                ("TCP_KEEPCNT", 3),      # 3 次无响应判死
            ):
                if hasattr(socket, opt):
                    try:
                        sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, opt), val)
                    except OSError:
                        pass
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("设置 keepalive 失败：%s", err)

    async def _watch(self, writer: asyncio.StreamWriter, reader: asyncio.StreamReader) -> None:
        """后台监听：对端断开（EOF）或读出错时，标记连接失效并触发重连。"""
        dropped = False
        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    _LOGGER.info("TCP 对端已断开 %s:%d", self._host, self._port)
                    dropped = True
                    break
                # 只发送、无反馈：收到的数据丢弃（仅记 debug）
                _LOGGER.debug("TCP 收到(忽略) %s", bytes(data).hex(" "))
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("TCP 读监听结束：%s", err)
            dropped = True
        finally:
            if self._writer is writer:
                self._writer = None
                if dropped and not self._closing:
                    self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        """启动后台重连循环（保持长连接不断）。"""
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.ensure_future(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """不断尝试重连（退避）。空闲时也保持与服务端的连接。"""
        delay = 2
        while not self._closing:
            # 最小间隔，避免对端“接受后立即关闭”时过于频繁地重连
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            if self._closing:
                return
            if self._writer is not None and not self._writer.is_closing():
                return
            try:
                async with self._lock:
                    if self._closing:
                        return
                    if self._writer is None or self._writer.is_closing():
                        await self._open()
                return
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "TCP 重连 %s:%d 失败：%s（%ds 后重试）",
                    self._host, self._port, err, delay,
                )
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    raise
                delay = min(delay * 2, 30)

    async def _open(self) -> None:
        """建立长连接（含 keepalive + 后台监听）。"""
        self._writer = None
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port), timeout=5
        )
        self._apply_keepalive(writer)
        self._writer = writer
        self._reader_task = asyncio.ensure_future(self._watch(writer, reader))
        _LOGGER.info(
            "TCP 已连接 %s:%d（长连接，配置：%s）", self._host, self._port, self._target
        )

    async def _ensure(self) -> None:
        """确保长连接可用（断了就重连）。"""
        w = self._writer
        if w is not None and not w.is_closing() and not w.transport.is_closing():
            return
        await self._open()

    async def connect(self) -> None:
        """建立连接。长连接模式保持；短连接模式仅做连通性预检。"""
        if not self._persistent:
            async with self._lock:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port), timeout=5
                )
                _LOGGER.info(
                    "TCP 连通性正常 %s:%d（短连接模式，配置：%s）",
                    self._host, self._port, self._target,
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:  # noqa: BLE001
                    pass
            return
        async with self._lock:
            try:
                await self._open()
            except Exception:
                # 首次连接失败也启动后台重连，持续尝试
                self._schedule_reconnect()
                raise

    # ---------- 对外发送 ----------
    async def write_bytes(self, data: bytes) -> None:
        """发送一段字节。长连接复用；短连接每次新建。失败都重试一次。"""
        if not self._persistent:
            await self._write_oneshot(data)
            return
        async with self._lock:
            last_err: Exception | None = None
            for attempt in (1, 2):
                try:
                    await self._ensure()
                    assert self._writer is not None
                    self._writer.write(bytes(data))
                    await self._writer.drain()
                    _LOGGER.debug(
                        "已发送(%s:%d): %s", self._host, self._port, bytes(data).hex(" ")
                    )
                    return
                except Exception as err:  # noqa: BLE001
                    last_err = err
                    self._writer = None
                    if attempt == 2:
                        raise
                    _LOGGER.warning("TCP 发送失败，重连后重试：%s", err)
            if last_err:
                raise last_err

    async def _write_oneshot(self, data: bytes) -> None:
        """短连接：新建 → 发送 → 关闭。"""
        async with self._lock:
            writer = None
            try:
                _reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port), timeout=5
                )
                writer.write(bytes(data))
                await writer.drain()
                await asyncio.sleep(0.05)
                _LOGGER.debug(
                    "已发送(%s:%d): %s", self._host, self._port, bytes(data).hex(" ")
                )
            finally:
                if writer is not None:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:  # noqa: BLE001
                        pass

    async def close(self) -> None:
        """关闭连接与后台任务。"""
        self._closing = True
        for task in (self._reader_task, self._reconnect_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._reader_task = None
        self._reconnect_task = None
        w = self._writer
        self._writer = None
        if w is not None:
            try:
                w.close()
                await w.wait_closed()
            except Exception:  # noqa: BLE001
                pass
