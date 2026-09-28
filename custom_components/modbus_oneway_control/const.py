"""常量定义：modbus 单向控制（USB 转 RS232/485，只发送不接收反馈）。

支持两类实体：
  - 开关类（switch）：开 → 发 on_code，关 → 发 off_code
  - 按钮类（button）：按一下 → 只发一条命令 command
"""

DOMAIN = "modbus_oneway_control"

# ---- 配置条目（config entry）字段 ----
CONF_DEVICE = "device"          # 硬件标识符：串口路径(/dev/...) 或 TCP 地址(tcp://host:port)
CONF_BAUD = "baud_rate"         # 波特率
CONF_NAME = "name"              # 本台设备的显示名（多台同型号 USB 时便于区分）

DEFAULT_BAUD = 9600
DEFAULT_TITLE = "modbus单向控制"

# 支持的波特率
BAUD_RATES = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]

# ---- configuration.yaml 字段 ----
CONF_CHANNELS = "channels"      # 开关类（单设备写法：顶层 channels）
CONF_BUTTONS = "buttons"        # 按钮类（单设备写法：顶层 buttons）
CONF_DEVICES = "devices"        # 多设备写法：devices: [{device|name, channels, buttons}]
CONF_ON_CODE = "on_code"
CONF_OFF_CODE = "off_code"
CONF_COMMAND = "command"        # 按钮类：按下时发送的那一条命令

MIN_BAUD = 1200
MAX_BAUD = 115200

# 传输类型
TRANSPORT_SERIAL = "serial"
TRANSPORT_TCP = "tcp"

# TCP 是否保持长连接（默认 True：与服务端始终连接、断开自动重连）
CONF_PERSISTENT = "persistent_tcp"
DEFAULT_PERSISTENT = True

# 超时（秒）
SERIAL_TIMEOUT = 2.0
