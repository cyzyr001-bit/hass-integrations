"""常量定义：Forick（弗雷克）八键智能开关面板。

设备：K7x 八键开关面板，Modbus RTU 从站，经 485 网转串以 TCP 透传接入。
每个面板：4 路继电器 + 8 个按键 + 指示灯 + 红外感应（人体感应）。
"""
from __future__ import annotations

DOMAIN = "forick_k7x"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_UNIT_ID = "unit_id"
CONF_UNIT_IDS = "unit_ids"  # 逗号分隔的多从站地址（解析后）
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_PORT = 8090
DEFAULT_UNIT_ID = 1
# 轮询间隔，单位：分钟。面板按键/红外/继电器物理变化靠「主动上报 + 总线监听」实时获取，
# 轮询仅作兜底（初始同步 + 异常恢复），故默认 30 分钟。
DEFAULT_SCAN_INTERVAL = 30
DEFAULT_TITLE = "八键智能开关(Forick)"

RELAY_COUNT = 4   # 4 路继电器
KEY_COUNT = 8     # 8 个按键


def parse_unit_ids(value) -> list[int]:
    """把「逗号分隔的从站地址」解析成 int 列表。

    支持：整数、逗号分隔（如 "1,2,3"）、空白分隔、中文逗号、
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


# ---- 寄存器（协议 V1.0，地址 0 起，功能码 03 读 / 06 或 10 写）----
REG_INDICATOR = 50       # 指示灯状态（低8位，每 bit 一路，0=关 1=开）
REG_KEY_BASE = 51        # 按键状态 51~58（低8位=事件码，只读）
REG_IR_STATUS = 59       # 红外状态 00=未触发 01=触发（只读）
REG_IR_CONFIG = 61       # 红外配置（读/写）
REG_DEV_TYPE = 64        # 设备类型（只读，06=八键型）
REG_REPORT_MODE = 65     # 上报标志 00=主动 01=被动（读/写）
REG_VERSION = 66         # 版本号（只读）

# 继电器 N → 按键绑定寄存器（值：0=无绑定，1~8=按键号）
REG_RELAY_BIND = {1: 69, 2: 70, 3: 71, 4: 77}
# 继电器 N → 输出寄存器（值：0=断开，1=闭合）
REG_RELAY_OUT = {1: 72, 2: 73, 3: 74, 4: 78}

# 继电器反馈状态（83 = 0x53）：00=不反馈，01=反馈（继电器动作后主动上报）
REG_RELAY_FEEDBACK = 83

# 块读取边界（实测设备单次最多约 17 个寄存器，超过会超时，分两块读）
READ_BLOCK1_START = 50   # 50~66（指示灯 + 按键 + 红外 + 配置 + 版本）
READ_BLOCK1_COUNT = 17
READ_BLOCK2_START = 69   # 69~83（继电器绑定 + 输出 + 反馈）
READ_BLOCK2_COUNT = 15

# 按键事件码映射：文档 raw → 用户事件码（1=单击 2=双击 3=长按）
# 文档编码：01=单击 02=双击 03=3击 04=长按按下 05=长按松开
KEY_EVENT_MAP = {
    1: 1,   # 单击
    2: 2,   # 双击
    3: 2,   # 3击 → 归入双击
    4: 3,   # 长按按下
    5: 3,   # 长按松开
}

# 按键事件中文名（用于传感器属性展示）
KEY_EVENT_NAMES = {1: "单击", 2: "双击", 3: "长按"}

# 写入后保持"乐观状态"的兜底秒数；实际 TTL 由 coordinator 按 scan_interval+15 动态计算
OPTIMISTIC_TTL_MIN = 10.0

# 按键事件在传感器上显示 N 秒后自动清零（按键是瞬态事件，清零后自动化才能每次按键都触发）
KEY_EVENT_RESET_SECONDS = 2.0

# 人体感应（红外）触发后 N 秒自动复位为未触发
IR_RESET_SECONDS = 5.0
