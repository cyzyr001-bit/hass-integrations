# TCP 继电器 (CORX)

通用 **TCP 继电器模块**（Modbus RTU over TCP）的 Home Assistant 自定义集成。
通过网转串透传，用 Modbus 线圈控制继电器。

## 功能

| 实体 | 类型 | 说明 |
|---|---|---|
| 继电器 1~N | `switch` | 每路一个开关实体（默认 4 路，最多 48 路）|

支持**多个从站**：从站地址用逗号分隔，每个模块生成一组开关。

## 安装

```bash
cp -r custom_components/tcp_relay /config/custom_components/
```

重启 Home Assistant → 「设置 → 设备与服务 → 添加集成」→ 搜索 **TCP继电器(CORX)**。

HACS：以「自定义仓库」添加本仓库，类型选 Integration，路径填 `custom_components/tcp_relay`。

## 配置

| 字段 | 默认 | 说明 |
|---|---|---|
| 主机 (host) | — | 网转串设备 IP |
| 端口 (port) | `50000` | TCP 端口 |
| 从站地址 (unit_id) | `1` | Modbus 站号；**多个用逗号分隔** |
| 路数 (channels) | `4` | 继电器路数（1~48）|
| 轮询间隔 (scan_interval) | `30` | 单位**分钟** |

## 协议说明

- 标准 Modbus RTU over TCP（RTU 透传，带 CRC16）
- 线圈地址从 `0` 起，逐路递增

## 要求

- Home Assistant ≥ 2023.1.0
- 设备需支持 **TCP Server / 网转串透传** 模式

## License

MIT
