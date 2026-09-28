# Modbus 单向控制（modbus_oneway_control）

面向 **USB 转 RS232 / RS485** 的 Home Assistant 自定义集成 —— **只发送、不接收反馈**。
用于控制功放开关、时序器、继电器等只需下发命令、无需读回状态的设备。

## 功能

| 类型 | 说明 |
|---|---|
| **开关类**（switch） | 开 → 发送 `on_code`；关 → 发送 `off_code` |
| **按钮类**（button） | 按下 → 只发送一条 `command` |

- 支持 **多台 USB 设备**（每台添加一次集成）
- 不读回设备状态；开关状态为本机记录，HA 重启后自动恢复
- 代码支持多种书写格式（十六进制 / 文本 / 转义）

## 安装

### 手动安装

```bash
cp -r custom_components/modbus_oneway_control /config/custom_components/
```

重启 Home Assistant 后，在「设置 → 设备与服务 → 添加集成」里搜索 **Modbus 单向控制**。

### HACS

以「自定义仓库」添加本仓库，类型选 **Integration**，仓库路径填写 `custom_components/modbus_oneway_control`。

## 配置

### 第一步：添加集成

每台 USB 转串口设备添加一次，填写：

| 字段 | 说明 |
|---|---|
| USB 硬件标识符 | 建议用稳定名，如 `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` |
| 波特率 | 1200 ~ 115200（默认 9600）|
| 显示名 | 可选，用于区分同型号设备 |

> ⚠️ `/dev/ttyUSB0`、`ttyUSB1` 会随插拔顺序变化，**强烈建议用 `/dev/serial/by-id/...` 稳定名**。
> 查看稳定名：在 HA 终端执行 `ls /dev/serial/by-id/`。

### 第二步：在 configuration.yaml 配置实体

```yaml
modbus_oneway_control:
  devices:
    - device: "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
      # 开关类：开→on_code，关→off_code
      channels:
        - name: "功放1"
          on_code: "A0 01 01 A2"
          off_code: "A0 01 00 A1"
        - name: "功放2"
          on_code: "A0 02 01 A3"
          off_code: "A0 02 00 A2"
      # 按钮类：按下→只发一条 command
      buttons:
        - name: "全开"
          command: "A0 FF 01 FF"
        - name: "全关"
          command: "A0 FF 00 FF"
```

**写多少条，就出现多少个实体。**

单台设备也可用顶层写法：

```yaml
modbus_oneway_control:
  channels:
    - {name: "功放1", on_code: "A0 01 01 A2", off_code: "A0 01 00 A1"}
  buttons:
    - {name: "全开", command: "A0 FF 01 FF"}
```

### 代码书写格式

| 写法 | 实际发送 |
|---|---|
| `"A0 01 01 A2"` | `a0 01 01 a2` |
| `"A0,01,01,A2"` | `a0 01 01 a2` |
| `"A00101A2"` | `a0 01 01 a2` |
| `"0xA0 0x01 0x01 0xA2"` | `a0 01 01 a2` |
| `"PWR=ON\r\n"` | ASCII 文本 + 回车换行 |
| `"AT+ON"` | UTF-8 文本命令 |

> ⚠️ YAML 中写 `\r\n` 必须用**双引号**。

## 实体

- `switch.<设备名>_<通道名>` —— 开关类
- `button.<设备名>_<按钮名>` —— 按钮类

无反馈：开关为乐观状态，本机记录并在重启后恢复。

## 要求

- Home Assistant ≥ 2023.1.0
- USB 转 RS232 / RS485 转换器（CH340 / FT232 / CP2102 等均可）
- 无需额外 Python 依赖（使用 HA 自带的 pyserial）

## License

MIT
