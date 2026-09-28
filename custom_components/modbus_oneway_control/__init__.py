"""modbus单向控制：USB 转 RS232/485，只发送不接收反馈。

设计（支持多台 USB）：
  - **每个 USB 设备添加一次集成**（config flow），各自填写：USB 硬件标识符、波特率、显示名。
  - 路数/命令在 /homeassistant/configuration.yaml 的 modbus_oneway_control: 段里配置：
      * 单设备写法：顶层直接写 channels（开关类）/ buttons（按钮类）
      * 多设备写法：写 devices，每台用 device 路径 或 name 与配置条目对应
    写多少路/多少个按钮，就出现多少个实体。
  - 开关类：开 → 发 on_code，关 → 发 off_code
  - 按钮类：按下 → 只发一条 command
  - 无反馈：只发送，不读回设备状态；开关状态为本机记录（重启后恢复）。
"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_BAUD,
    CONF_BUTTONS,
    CONF_CHANNELS,
    CONF_COMMAND,
    CONF_DEVICE,
    CONF_DEVICES,
    CONF_NAME,
    CONF_OFF_CODE,
    CONF_ON_CODE,
    DEFAULT_BAUD,
    DOMAIN,
)
from .serial_api import Channel, SerialWriter, parse_code
from .tcp_api import TcpWriter, is_tcp

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["switch", "button"]

# 开关类：每路
CHANNEL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_ON_CODE): cv.string,
        vol.Optional(CONF_OFF_CODE, default=""): cv.string,
    }
)

# 按钮类：每个按钮
BUTTON_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_COMMAND): cv.string,
    }
)

# 每台设备分组 schema（device / name 二选一，用于匹配配置条目）
DEVICE_GROUP_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_DEVICE): cv.string,
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional(CONF_CHANNELS, default=[]): vol.All(
            cv.ensure_list, [CHANNEL_SCHEMA]
        ),
        vol.Optional(CONF_BUTTONS, default=[]): vol.All(
            cv.ensure_list, [BUTTON_SCHEMA]
        ),
    }
)

# 顶层 schema：既支持顶层 channels/buttons（单设备），也支持 devices（多设备）
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_CHANNELS, default=[]): vol.All(
                    cv.ensure_list, [CHANNEL_SCHEMA]
                ),
                vol.Optional(CONF_BUTTONS, default=[]): vol.All(
                    cv.ensure_list, [BUTTON_SCHEMA]
                ),
                vol.Optional(CONF_DEVICES, default=[]): vol.All(
                    cv.ensure_list, [DEVICE_GROUP_SCHEMA]
                ),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


def _parse_channels(raw: list[dict]) -> list[Channel]:
    """把 YAML 里开关类的路配置解析成 Channel 列表（解析失败的路跳过并告警）。"""
    out: list[Channel] = []
    for idx, item in enumerate(raw or [], start=1):
        name = str(item.get(CONF_NAME, f"通道{idx}")).strip()
        try:
            on_code = parse_code(item.get(CONF_ON_CODE, ""))
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("开关 %s 的开代码无效，已跳过：%s", name, err)
            continue
        off_raw = item.get(CONF_OFF_CODE, "")
        try:
            off_code = parse_code(off_raw) if str(off_raw).strip() else b""
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("开关 %s 的关代码无效，已跳过：%s", name, err)
            continue
        out.append(Channel(name=name, on_code=on_code, off_code=off_code))
    return out


def _parse_buttons(raw: list[dict]) -> list[Channel]:
    """把 YAML 里按钮类配置解析成 Channel（复用结构：on_code 存那条命令）。"""
    out: list[Channel] = []
    for idx, item in enumerate(raw or [], start=1):
        name = str(item.get(CONF_NAME, f"按钮{idx}")).strip()
        try:
            cmd = parse_code(item.get(CONF_COMMAND, ""))
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("按钮 %s 的命令无效，已跳过：%s", name, err)
            continue
        out.append(Channel(name=name, on_code=cmd, off_code=b""))
    return out


def _entry_keys(entry: ConfigEntry) -> set[str]:
    """一台设备可能被 YAML 用 设备路径 或 显示名 引用，返回全部可比对键。"""
    data = {**entry.data, **entry.options}
    keys = {
        str(data.get(CONF_DEVICE, "")).strip(),
        str(data.get(CONF_NAME, "")).strip(),
        (entry.title or "").strip(),
    }
    return {k for k in keys if k}


def _pick_group(hass: HomeAssistant, entry: ConfigEntry) -> dict | None:
    """若 YAML 用了 devices 分组，返回匹配本条目的一组；否则 None。"""
    groups: list[dict] = hass.data[DOMAIN].get("groups", [])
    if not groups:
        return None
    keys = _entry_keys(entry)
    for g in groups:
        gkeys = {
            str(g.get(CONF_DEVICE, "")).strip(),
            str(g.get(CONF_NAME, "")).strip(),
        }
        gkeys = {k for k in gkeys if k}
        if gkeys == set():
            # 分组没写 device/name：只有一台设备时兜底给它
            if len(hass.config_entries.async_entries(DOMAIN)) == 1:
                return g
            continue
        if gkeys & keys:
            return g
    return None


def _select_entities(
    hass: HomeAssistant, entry: ConfigEntry
) -> tuple[list[Channel], list[Channel]]:
    """给某配置条目挑选属于它的（开关列表, 按钮列表）。"""
    groups: list[dict] = hass.data[DOMAIN].get("groups", [])
    if groups:
        g = _pick_group(hass, entry)
        if g is None:
            return [], []
        return g.get("_channels", []), g.get("_buttons", [])

    # 没有 devices 分组：回退顶层 channels / buttons
    return (
        hass.data[DOMAIN].get("channels", []),
        hass.data[DOMAIN].get("buttons", []),
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """解析 configuration.yaml 中的 modbus_oneway_control: 段并缓存。"""
    hass.data.setdefault(DOMAIN, {})
    conf = config.get(DOMAIN) or {}
    raw = conf.get(CONF_CHANNELS, [])
    raw_btn = conf.get(CONF_BUTTONS, [])
    groups_raw = conf.get(CONF_DEVICES, [])

    hass.data[DOMAIN]["channels"] = _parse_channels(raw)
    hass.data[DOMAIN]["buttons"] = _parse_buttons(raw_btn)

    groups: list[dict] = []
    for g in groups_raw:
        item = dict(g)
        item["_channels"] = _parse_channels(g.get(CONF_CHANNELS, []))
        item["_buttons"] = _parse_buttons(g.get(CONF_BUTTONS, []))
        groups.append(item)
    hass.data[DOMAIN]["groups"] = groups

    _LOGGER.info(
        "modbus单向控制：YAML 读到顶层 开关 %d / 按钮 %d、设备分组 %d 台"
        "（共 开关 %d / 按钮 %d）",
        len(hass.data[DOMAIN]["channels"]),
        len(hass.data[DOMAIN]["buttons"]),
        len(groups),
        sum(len(g["_channels"]) for g in groups),
        sum(len(g["_buttons"]) for g in groups),
    )
    _async_log_serial_ports(hass)
    return True


def _list_serial_ports() -> dict[str, list[str]]:
    """列出 /dev/ttyUSB* 与 /dev/serial/by-id/* 稳定名，便于多 USB 排查。"""
    import glob

    return {
        "ttyUSB": sorted(glob.glob("/dev/ttyUSB*")),
        "by-id": sorted(glob.glob("/dev/serial/by-id/*")),
    }


def _async_log_serial_ports(hass: HomeAssistant) -> None:
    """把可用串口写进日志（异步跑，不阻塞启动）。"""

    async def _run() -> None:
        try:
            ports = await hass.async_add_executor_job(_list_serial_ports)
            _LOGGER.info(
                "modbus单向控制：检测到串口 %s | 稳定名(by-id) %s",
                ports.get("ttyUSB") or "无",
                ports.get("by-id") or "无",
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("列出串口失败：%s", err)

    hass.async_create_task(_run())


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """建立配置条目：打开传输（串口/TCP）+ 建立实体。"""
    hass.data.setdefault(DOMAIN, {})
    data = {**entry.data, **entry.options}
    device = data.get(CONF_DEVICE)
    baud = int(data.get(CONF_BAUD, DEFAULT_BAUD))

    channels, buttons = _select_entities(hass, entry)
    if not channels and not buttons:
        has_groups = bool(hass.data[DOMAIN].get("groups"))
        _LOGGER.warning(
            "modbus单向控制：条目「%s」(%s) 没有对应到任何开关/按钮。%s",
            entry.title,
            device,
            "请在 configuration.yaml 的 modbus_oneway_control: devices 里为其配置"
            " channels/buttons"
            if has_groups
            else "请在 configuration.yaml 的 modbus_oneway_control: 里配置 channels/buttons",
        )
    else:
        _LOGGER.info(
            "modbus单向控制：条目「%s」(%s) 绑定 开关 %d %s / 按钮 %d %s",
            entry.title,
            device,
            len(channels),
            [c.name for c in channels],
            len(buttons),
            [b.name for b in buttons],
        )

    # 依据地址选择传输方式：tcp://... → TCP；其余 → 串口
    use_tcp = is_tcp(device)
    if use_tcp:
        writer = TcpWriter(hass, device)
    else:
        writer = SerialWriter(hass, device, baud)
    try:
        await writer.connect()
    except Exception as err:  # noqa: BLE001
        _LOGGER.error(
            "连接 %s 失败：%s（稍后发送时会重试）", device, err
        )

    hass.data[DOMAIN][entry.entry_id] = {
        "writer": writer,
        "channels": channels,
        "buttons": buttons,
        "device": device,
        "baud": baud,
        "transport": "tcp" if use_tcp else "serial",
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """选项变更时重载。"""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载配置条目：关闭串口。"""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        store = hass.data[DOMAIN].pop(entry.entry_id, None)
        if store and store.get("writer"):
            await store["writer"].close()
    return unload_ok
