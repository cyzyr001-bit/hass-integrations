"""常量定义：智道物联 6路可控硅调光模块。"""

DOMAIN = "smart_lighting_485"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_UNIT_ID = "unit_id"
CONF_UNIT_IDS = "unit_ids"  # 逗号分隔的多从站地址（解析后）
CONF_CHANNELS = "channels"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_DIMMING = "dimming"
CONF_BRIGHTNESS_MAX = "brightness_max"
CONF_GAMMA = "gamma"

DEFAULT_PORT = 8010
DEFAULT_UNIT_ID = 1
DEFAULT_CHANNELS = 6
DEFAULT_DIMMING = True
DEFAULT_SCAN_INTERVAL = 30
DEFAULT_BRIGHTNESS_MAX = 100   # 调光寄存器以百分比输出（0=关，1~100=亮度）
DEFAULT_GAMMA = 1.0            # 调光曲线；1=线性(HA 0~255 直映设备 0~100)
DEFAULT_TITLE = "TCP可控硅模块"


def parse_unit_ids(value) -> list[int]:
    """把「逗号分隔的从站地址」解析成 int 列表。

    支持：整数、逗号分隔（如 "1,2,3,4,5,6"）、空白分隔、中文逗号、
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
        try:
            uid = int(part)
        except ValueError:
            continue
        if 1 <= uid <= 247 and uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out

# ---- 寄存器（协议 V1.9，地址 0 起）----
REG_DEVICE_ADDR = 129       # 设备地址（Modbus 站号）
REG_CHANNEL_NUM = 130       # 路数 1~16
REG_SWITCH_BASE = 159       # 第 1 路亮度/开关，第 n 路 = 159 + (n-1)，0=关，1~100=亮度
REG_STATUS_BITMAP = 197     # 开关状态位图，bit15→第1路（只读）
REG_TIME_BASE = 153         # 年/月/日/时/分/秒（6 个寄存器）

# 开关状态位图中第 1 路对应的 bit
BITMAP_BIT_CH1 = 15

# 写入后保持“乐观状态”的兜底秒数（避免刚关灯时被一次脏数据读回 on）；
# 实际 TTL 由 coordinator 按 scan_interval+15 动态计算，此常量仅作最低保底
OPTIMISTIC_TTL_MIN = 10.0
