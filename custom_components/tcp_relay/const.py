"""常量定义：TCP 继电器模块（Modbus RTU over TCP，线圈控制）。"""

DOMAIN = "tcp_relay"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_UNIT_ID = "unit_id"
CONF_UNIT_IDS = "unit_ids"  # 逗号分隔的多从站地址（解析后）
CONF_CHANNELS = "channels"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_PORT = 50000
DEFAULT_UNIT_ID = 1
DEFAULT_CHANNELS = 4
DEFAULT_SCAN_INTERVAL = 30
DEFAULT_TITLE = "TCP继电器模块"


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

# ---- 线圈地址（协议 V1.2，地址 0 起）----
# 第 N 路线圈地址 = N - 1（第 1 路 = 0x0000）
COIL_BASE = 0
# 产品最多输出 48 路，但常用 4/8/16 路，配置里限制到 48
MAX_CHANNELS = 48

# 写入后保持"乐观状态"的兜底秒数（避免刚关时被一次脏数据读回 on）；
# 实际 TTL 由 coordinator 按 scan_interval+15 动态计算，此常量仅作最低保底
OPTIMISTIC_TTL_MIN = 10.0
