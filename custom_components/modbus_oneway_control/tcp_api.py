"""TCP 客户端传输：把命令通过 TCP 直连发送（USB 转串口网关 / 网转串设备）。

支持配置里写 ``tcp://host:port``（如 tcp://192.168.110.158:6677），
本模块用 asyncio 建立 TCP 客户端连接并发送字节，只发不收。

设计：**每次发送都新建一条连接，发完即关（connect-send-close）**。
这样最可靠——避免长连接被对端悄悄断开后，第一条命令写进死 socket 而静默丢失。
本集成是"只发送、无反馈"的低频命令（开关/按钮），连接开销可忽略。
"""
from __future__ import annotations

import asyncio
import logging

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
    # 兼容 tcp://host:port/ 或带路径
    body = body.split("/", 1)[0]
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
    """TCP 客户端写入器：每次发送新建连接、发完即关（最可靠）。"""

    def __init__(self, hass: HomeAssistant, target: str) -> None:
        self._hass = hass
        self._target = target
        self._host, self._port = parse_tcp(target)
        self._lock = asyncio.Lock()

    @property
    def target(self) -> str:
        return self._target

    async def connect(self) -> None:
        """连通性预检：尝试连接一次（失败抛异常；不保持连接）。"""
        async with self._lock:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), timeout=5
            )
            _LOGGER.info(
                "TCP 连通性正常 %s:%d（配置：%s）", self._host, self._port, self._target
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def write_bytes(self, data: bytes) -> None:
        """新建连接 → 发送 → 等 flush → 关闭（加锁串行）。"""
        async with self._lock:
            writer = None
            try:
                _reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port), timeout=5
                )
                writer.write(bytes(data))
                await writer.drain()
                # 短暂让出，确保数据进入内核缓冲
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
        """无长连接，无需关闭。"""
        return
