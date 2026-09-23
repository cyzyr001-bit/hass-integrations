"""常量定义：Forick（弗雷克）三合一环境面板（空调 + 地暖 + 新风）。

设备：三合一环境面板，Modbus RTU 从站，经 485 网转串以 TCP 透传接入。
一个设备（面板）包含 3 个实体：空调（climate）、地暖（climate）、新风（fan）。

协议（实测确认，与 K7x 相同的标准 Modbus RTU 双向）：
- 请求与响应均为标准 Modbus RTU：功能码 03 读保持寄存器 / 06 写单寄存器，
  带 CRC16。
- 从站地址 = 面板 ID（十六进制，面板上标 "23" = 0x23 = 十进制 35）。
- 寄存器地址 1 起（PDF 里的 Dec 序号 = 实际地址，非 0 起）。
"""
from __future__ import annotations

DOMAIN = "forick_panel"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_UNIT_ID = "unit_id"
CONF_UNIT_IDS = "unit_ids"  # 逗号分隔的多从站地址（解析后）
CONF_UNIT_ID_LABELS = "unit_id_labels"  # 逗号分隔的原始 ID 字符串（用于设备名显示）
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_PORT = 8010
DEFAULT_UNIT_ID = 0x23  # 面板 ID（十六进制）；"23" = 0x23 = 十进制 35
DEFAULT_UNIT_ID_STR = "23"  # UI 默认显示的面板 ID（十六进制字符串）
# 面板物理按键时经总线广播 06 写帧，实时性靠「总线监听」；轮询仅作兜底。
# 主人要求：温度传感器查询即轮询，轮询默认 5 分钟。
DEFAULT_SCAN_INTERVAL = 5
DEFAULT_TITLE = "三合一面板(Forick)"
DEVICE_NAME = "三合一温控面板"  # 设备名（主人指定）
MANUFACTURER = "智道物联"  # 制造商（主人指定，与八键开关一致）
MODEL = "三合一温控面板"  # 型号
MANUFACTURER_URL = "http://www.hfznjj.cn/"  # 制造商网址（与八键开关一致）

# ---- 寄存器（协议 V1.0，地址 1 起；PDF Dec 序号 = 实际地址）----
REG_AC_POWER = 0x0001    # 空调开关机：00=关机 01=开机
REG_AC_MODE = 0x0002     # 空调模式：00=制冷 01=制热 02=除湿 03=送风
REG_AC_FAN = 0x0003      # 空调风速：01=低速 02=中速 03=高速
REG_AC_TEMP = 0x0004     # 空调温度设定：10~32℃(0A~20H)，默认 25
REG_AC_VALVE = 0x0005    # 空调阀控制（00=无效/面板自控）
REG_HEAT_POWER = 0x0006  # 地暖开关机：00=关机 01=开机
REG_HEAT_TEMP = 0x0007   # 地暖温度设定：10~32℃，默认 25
REG_HEAT_VALVE = 0x0008  # 地暖阀控制（00=无效/面板自控）
REG_FA_POWER = 0x0009    # 新风开关机：00=关机 01=开机
REG_FA_PARAM = 0x000A    # 新风参数设定值（预留）：0~9999
REG_FA_FAN = 0x000B      # 新风风速：01=低速 02=中速 03=高速
REG_COMP_TEMP = 0x000C   # 补偿温度：-9~9℃
REG_ROOM_TEMP = 0x000D   # 实时温度：值/10（0000H~0258H=0~60.0℃，0FECH~FFFH=-20.0~-1.0℃）
REG_ROOM_HUMID = 0x000E  # 实时湿度：0~100%
REG_FA_REALTIME = 0x000F  # 新风实时参数

# 温度设定范围（℃）
TEMP_MIN = 10
TEMP_MAX = 32

# 空调模式 → 寄存器值（制冷/制热/除湿/送风）
AC_MODE_COOL = 0
AC_MODE_HEAT = 1
AC_MODE_DRY = 2
AC_MODE_FAN = 3

# 空调/新风风速 → 寄存器值
FAN_LOW = 1
FAN_MEDIUM = 2
FAN_HIGH = 3

# 写入后保持"乐观状态"的兜底秒数；实际 TTL 由 coordinator 按 scan_interval 动态计算
OPTIMISTIC_TTL_MIN = 10.0


def parse_unit_ids(value) -> list[int]:
    """把「逗号分隔的面板 ID（十六进制）」解析成 int 列表。

    面板 ID 是十六进制（与八键开关一致，弗雷克面板上用 16 进制标注），
    例如面板上标 "23" 表示 0x23 = 十进制 35。本函数把每个 ID 按十六进制
    解析成从站地址（int），再交给 Modbus 帧使用。

    支持：整数、逗号分隔（如 "23,24"）、空白分隔、中文逗号、
    以及单个 int / list。非法值静默过滤。
    """
    if value is None:
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, (list, tuple)):
        ids: list[int] = []
        for item in value:
            ids.extend(parse_unit_ids(item))
        return ids
    text = str(value)
    text = text.replace("，", ",").replace("；", ",").replace("、", ",")
    parts = [p for chunk in text.split(",") for p in chunk.split()]
    out: list[int] = []
    seen: set[int] = set()
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 去掉可能的 0x 前缀，按十六进制解析
        raw = part[2:] if part.lower().startswith("0x") else part
        try:
            uid = int(raw, 16)
        except ValueError:
            continue
        if 1 <= uid <= 247 and uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out


def parse_unit_id_labels(value) -> list[str]:
    """把「逗号分隔的面板 ID」解析成原始字符串列表（用于设备名显示）。

    与 parse_unit_ids 对应：保留用户输入的原始十六进制字符串（如 "23"），
    不转十进制。顺序与 parse_unit_ids 一致（只保留能成功解析的项）。
    """
    if value is None:
        return []
    if isinstance(value, int):
        return [f"{value:X}"]
    if isinstance(value, (list, tuple)):
        labels: list[str] = []
        for item in value:
            labels.extend(parse_unit_id_labels(item))
        return labels
    text = str(value)
    text = text.replace("，", ",").replace("；", ",").replace("、", ",")
    parts = [p for chunk in text.split(",") for p in chunk.split()]
    out: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        raw = part[2:] if part.lower().startswith("0x") else part
        try:
            int(raw, 16)
        except ValueError:
            continue
        # 统一成大写无前导零的十六进制字符串（"23" 保持 "23"，"0x23" 归一为 "23"）
        label = raw.lstrip("0").upper() or "0"
        if label not in out:
            out.append(label)
    return out
