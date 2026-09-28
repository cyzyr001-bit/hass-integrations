# TCP 可控硅调光 (CNHQ)

**485 可控硅调光模块** 的 Home Assistant 自定义集成。
通过 **485 网转串（Modbus RTU over TCP）** 控制多通道灯光亮度。

## 功能

| 实体 | 类型 | 说明 |
|---|---|---|
| 通道 1~N | `light` | 每个可控硅通道一个**可调光灯**实体（默认 6 路，最多 16 路）|

支持**多个从站**：从站地址用逗号分隔，每个模块生成一组灯。

## 安装

```bash
cp -r custom_components/smart_lighting_485 /config/custom_components/
```

重启 Home Assistant → 「设置 → 设备与服务 → 添加集成」→ 搜索 **TCP可控硅(CNHQ)**。

HACS：以「自定义仓库」添加本仓库，类型选 Integration，路径填 `custom_components/smart_lighting_485`。

## 配置

| 字段 | 默认 | 说明 |
|---|---|---|
| 主机 (host) | — | 485 网转串设备 IP |
| 端口 (port) | `8010` | TCP 端口 |
| 从站地址 (unit_id) | `1` | Modbus 站号；**多个用逗号分隔** |
| 路数 (channels) | `6` | 通道数（1~16）|
| 调光 (dimming) | `True` | 是否启用亮度调节（关闭则为纯开关）|
| 最大亮度 (brightness_max) | `100` | 设备侧亮度上限（百分比 0~100）|
| 伽马 (gamma) | `1.0` | 调光曲线；1.0 = 线性（HA 0~255 直映设备 0~100）|
| 轮询间隔 (scan_interval) | `30` | 单位**分钟** |

## 协议说明

- 标准 Modbus RTU over TCP（RTU 透传，带 CRC16）
- 设备地址寄存器 `129`、通道数寄存器 `130`（可用于读取设备信息）
- 调光寄存器以百分比输出：`0` = 关，`1~100` = 亮度

## 要求

- Home Assistant ≥ 2023.1.0
- 设备需支持 **TCP Server / 网转串透传** 模式

## License

MIT
