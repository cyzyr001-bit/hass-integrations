# Home Assistant 集成合集

本项目集中存放 5 个自研的 Home Assistant 自定义集成，主要面向国内常见的 485 网转串 / TCP 智能硬件设备，以及涂鸦（Tuya）云灯组。

## 集成列表

| 目录 | 集成名称 | 说明 | 通信方式 |
|---|---|---|---|
| `custom_components/forick_k7x` | 八键智能开关 (Forick) | 八键面板 + 4 继电器，支持按键实时反馈、红外人体感应 | 本地 TCP (Modbus RTU over TCP) |
| `custom_components/tcp_relay` | TCP 继电器 (CORX) | 通用 TCP 继电器开关控制 | 本地 TCP (Modbus RTU over TCP) |
| `custom_components/smart_lighting_485` | TCP 可控硅调光 (CNHQ) | 485 可控硅调光，多通道亮度控制 | 本地 TCP (Modbus RTU over TCP) |
| `custom_components/tuya_cloud_groups` | Tuya Cloud Groups | 涂鸦云灯组（按账号家庭自动同步） | 涂鸦开放平台 API（云） |
| `custom_components/tuya_web_groups` | Tuya Web Groups | 涂鸦灯组（基于 Web API） | 涂鸦 Web API（云） |

## 安装方式

### 方式一：手动安装（推荐本仓库）

将需要的集成目录复制到 Home Assistant 的 `custom_components/` 目录下，然后重启 HA 并在「设置 → 设备与服务」中添加集成。

例如安装八键智能开关：

```bash
# 假设已 clone 本仓库到本地
cp -r custom_components/forick_k7x /config/custom_components/
```

### 方式二：HACS

> ⚠️ HACS 的自定义仓库「一个仓库只识别一个集成」。本仓库是 monorepo，含 5 个集成，**HACS 直接添加整个仓库只会识别根目录下的一个集成**。
>
> 如需通过 HACS 安装，建议：
> - 在 HACS 中以「自定义仓库」添加本仓库，类型选 **Integration**，仓库路径填写对应集成的子目录；或
> - 将某个集成拆分为独立仓库后添加。

## 通用要求

- Home Assistant 版本 ≥ 2023.1.0
- 485 系列集成需要设备支持「TCP Server / 网转串」模式，并在集成配置中填写设备的 IP 与端口

## 各集成详细说明

各集成配置项与寄存器协议说明，请见对应集成目录内的 `README.md`（如有）及 `manifest.json` 中的 `documentation` 字段。

## License

MIT
