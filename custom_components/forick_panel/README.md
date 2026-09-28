# 三合一面板 (Forick)

Forick（弗雷克）**三合一环境面板**（空调 + 地暖 + 新风）的 Home Assistant 自定义集成。
通过 **485 网转串 / TCP 隧道（Modbus RTU over TCP）** 通信。

## 功能

| 实体 | 类型 | 说明 |
|---|---|---|
| 空调 | `climate` | 开关机、模式（制冷/制热/除湿/送风）、风速（低/中/高）、温度 10~32℃ |
| 地暖 | `climate` | 开关机、温度 10~32℃ |
| 新风 | `climate` | 开关机、风速 |
| 室温 | `sensor` | 面板采集的室内温度（随轮询刷新）|

支持**多个面板**：从站地址用逗号分隔即可（如 `21,22,23`），每个面板生成一套实体。

## 安装

```bash
cp -r custom_components/forick_panel /config/custom_components/
```

重启 Home Assistant → 「设置 → 设备与服务 → 添加集成」→ 搜索 **三合一面板(Forick)**。

HACS：以「自定义仓库」添加本仓库，类型选 Integration，路径填 `custom_components/forick_panel`。

## 配置

| 字段 | 默认 | 说明 |
|---|---|---|
| 主机 (host) | — | 网转串 / TCP 隧道地址 |
| 端口 (port) | `8010` | TCP 端口 |
| 面板 ID (unit_id) | `23` | **十六进制**面板地址；**多个用逗号分隔** |
| 轮询间隔 (scan_interval) | `5` | 单位**分钟** |

## 协议要点（实测）

- 请求（主机→面板）：标准 Modbus RTU，功能码 `03` 读 / `06` 写单寄存器，带 CRC16
- 响应（面板→主机）：**`55 AA` 帧**（网转串封装）
  ```
  55 AA LEN CMD DEV_ID(2) VALUE(N) CKSUM
  ```
  - `CMD 05` = 寄存器上报（读响应）、`CMD 01` = 心跳（忽略）
  - `CKSUM` = 前面所有字节之和 mod 256
- 网转串为**广播模式**，从站地址被忽略（装置 ID 用于区分面板）

## 要求

- Home Assistant ≥ 2023.1.0
- 设备需支持 **TCP Server / 网转串透传** 模式

## License

MIT
