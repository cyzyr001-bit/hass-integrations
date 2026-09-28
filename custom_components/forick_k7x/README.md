# 八键智能开关 (Forick)

Forick（弗雷克）**K7x 八键智能开关面板** 的 Home Assistant 自定义集成。
通过 **485 网转串（Modbus RTU over TCP）** 与本地面板通信。

## 功能

| 实体 | 类型 | 说明 |
|---|---|---|
| 继电器 1~4 | `switch` | 4 路继电器开 / 关（带状态回读）|
| 按键 | `sensor` | 8 个按键合一的**事件传感器**（单击 / 双击 / 长按）|
| 人体感应 | `binary_sensor` | 红外人体感应，触发后 **5 秒自动复位** |
| 继电器绑定 ×4 | `select` | 每个按键绑定到哪路继电器 |
| 继电器反馈 | `select` | 继电器动作后是否上报（寄存器 83）|
| 指示灯 | `select` | 面板指示灯（全灭 / 全亮，寄存器 50）|
| 红外配置 | `select` | 红外上报模式（寄存器 61）|

> 按键事件编码：键 N 单击 = `N*10+1`、双击 = `N*10+2`、长按 = `N*10+3`，无操作 = `0`。

## 安装

```bash
cp -r custom_components/forick_k7x /config/custom_components/
```

重启 Home Assistant → 「设置 → 设备与服务 → 添加集成」→ 搜索 **八键智能开关(Forick)**。

HACS：以「自定义仓库」添加本仓库，类型选 Integration，路径填 `custom_components/forick_k7x`。

## 配置

| 字段 | 默认 | 说明 |
|---|---|---|
| 主机 (host) | — | 485 网转串设备 IP |
| 端口 (port) | `8090` | TCP 端口 |
| 从站地址 (unit_id) | `1` | 面板 Modbus 站号；**多个用逗号分隔**（如 `1,2,3`）|
| 轮询间隔 (scan_interval) | `30` | 单位**分钟**（实时性主要靠总线监听，轮询仅兜底）|

## 说明

- 实时性：面板按键事件通过**总线监听**实时捕获，轮询只作兜底。
- 继电器绑定、反馈、指示灯等属于「配置」类实体（entity_category=config）。
- 依赖：无（仅用 HA 自带库 + 标准库实现 Modbus RTU over TCP）。

## 要求

- Home Assistant ≥ 2023.1.0
- 设备需支持 **TCP Server / 网转串透传** 模式

## License

MIT
