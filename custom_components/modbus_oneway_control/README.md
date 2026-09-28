# modbus单向控制（modbus_oneway_control）

面向 **USB 转 RS232/485** 或 **TCP 转串口网关** 的 Home Assistant 自定义集成 —— **只发送，不接收反馈**。
支持两类实体：**开关类**（开/关各发一条代码）+ **按钮类**（按一下只发一条命令）。

**两种传输方式**（按地址自动识别）：
- 串口：`/dev/ttyUSB0` 或 `/dev/serial/by-id/...`
- **TCP 客户端直连：`tcp://192.168.110.158:6677`**
  - 默认**长连接**：与服务端始终保持一条连接，**断线自动重连**（空闲也维持），并开启 TCP keepalive
  - 可在集成选项中关闭，改为「发完即关」（每次新建连接）

## 组件信息

| 项 | 值 |
|---|---|
| domain | `modbus_oneway_control` |
| 名称 | modbus单向控制 |
| 版本 | 1.2.0（TCP 长连接 + 自动重连）|
| 制造商 | **智道物联** |
| 传输 | 串口 / **TCP 客户端（tcp://host:port）** |
| 文档（集成的 ? 链接）| `http://www.hfznjj.cn/` |
| 图标 | `brand/` 目录（256×256 + 512×512）|

## 设计

1. **每台设备添加一次集成**（config flow）：**地址**（串口路径 或 `tcp://主机:端口`）+ 波特率（仅串口有效）+ 显示名（可选）
2. **实体在 `configuration.yaml` 里配**（`modbus_oneway_control:` 段）：**写多少条就出现多少个实体**
3. 两类实体：
   - **开关类 `channels`**：开 → 发 `on_code`，关 → 发 `off_code`
   - **按钮类 `buttons`**：按下 → 只发一条 `command`
4. 不读回设备状态；开关状态为本机记录（`RestoreEntity`，重启后恢复），前端是**普通单键开关**

## 两种传输方式（按地址自动识别）

| 地址写法 | 传输 | 说明 |
|---|---|---|
| `/dev/serial/by-id/usb-...` 或 `/dev/ttyUSB0` | USB 转串口 | 波特率生效 |
| **`tcp://192.168.110.158:6677`** | **TCP 客户端** | 波特率忽略；默认**长连接 + 自动重连** |

### TCP 长连接（默认开启）

- **始终保持与服务端的一条连接**，不会每条命令都重新握手
- 后台任务持续监听对端状态；**一旦断开，自动重连**（指数退避 2s→30s），空闲时也维持连接
- 开启 **TCP keepalive**（空闲 30s 探测）以及时发现半开连接
- 发送前会确认连接可用；发送失败会**重连后重试一次**，避免丢命令
- 选项里可关闭 → 改为「每次发送新建连接、发完即关」（适合对端会主动断开的场景）

> 📡 两种模式都不会“静默丢包”：长连接靠后台监听 + 发送前检查 + 失败重连；短连接靠每次新建。

## configuration.yaml 配置示例（TCP + 串口 + 两类实体）

```yaml
modbus_oneway_control:
  devices:
    # ---------- TCP 转串口网关（TCP 客户端直连）----------
    - device: "tcp://192.168.110.158:6677"
      channels:
        - name: "功放1"
          on_code: "A0 01 01 A2"
          off_code: "A0 01 00 A1"
      buttons:
        - name: "全开"
          command: "A0 FF 01 FF"
    # ---------- 本地 USB 转串口 ----------
    - device: "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
      channels:                       # 开关类
        - name: "功放2"
          on_code: "A0 02 01 A3"
          off_code: "A0 02 00 A2"
      buttons:                        # 按钮类
        - name: "全开"
          command: "A0 FF 01 FF"
        - name: "全关"
          command: "A0 FF 00 FF"
    # ---------- 第 2 台 ----------
    - device: "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0-2"
      channels:
        - name: "功放3"
          on_code: "A0 03 01 A4"
          off_code: "A0 03 00 A3"
      buttons:
        - name: "复位"
          command: "A0 00 00 A0"
```

> 单台也可用旧的顶层写法：`modbus_oneway_control:\n  channels: [...]\n  buttons: [...]`
> 分组里 `device:` 也可写 `name:`（与添加集成时的显示名一致）。

### 代码书写格式（开关代码和按钮命令都支持）
| 写法 | 解析结果 |
|---|---|
| `"A0 01 01 A2"` | `a0 01 01 a2` |
| `"A0,01,01,A2"` | `a0 01 01 a2` |
| `"A00101A2"` | `a0 01 01 a2` |
| `"0xA0 0x01 0x01 0xA2"` | `a0 01 01 a2` |
| `"PWR=ON\r\n"` | ASCII + CR LF |
| `"AT+ON"` | UTF-8 文本 |

> ⚠️ YAML 里写 `\r\n` 必须用**双引号**（双引号才会解析转义）。

## 实体

| entity_id | 类型 | 名称 |
|---|---|---|
| `switch.gong_fang_kong_zhi_qi_gong_fang_1` | 开关类 | 功放控制器 功放1 |
| `switch.gong_fang_kong_zhi_qi_gong_fang_2` | 开关类 | 功放控制器 功放2 |
| `button.gong_fang_kong_zhi_qi_quan_kai` | 按钮类 | 功放控制器 全开 |
| `button.gong_fang_kong_zhi_qi_quan_guan` | 按钮类 | 功放控制器 全关 |

## ★多台 USB 的关键：用稳定名★

`/dev/ttyUSB0`、`/dev/ttyUSB1` 会随插拔顺序变化，导致"换口打不开 / 串到另一台"。务必用**稳定名**：

```bash
ls /dev/serial/by-id/      # 例：/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
```

集成内已加**自动匹配兜底**：配置路径不存在时，按 by-id / 唯一串口自动改用并打 WARNING；
写入失败（I/O error）会自动**重开一次串口再重试**。

## 实测记录（2026-09-28 · HA 192.168.110.228:8123）

用本机 TCP 监听器做决定性验证：

**① 长连接复用** —— 连续 3 条命令都在**同一条连接**上送达（无新建）：
```
CONNECT #1  (保持)
  conn #1 RECV a0 01 01 a2
  conn #1 RECV a0 ff 01 ff
  conn #1 RECV a0 01 00 a1
```

**② 断线自动重连** —— 杀掉服务端后，无人操作，HA **约 7 秒自动重连**：
```
CLOSE #1  (服务端断)
CONNECT #1 at t=7.2s      ← 自动重连
  conn #1 RECV a0 ff 01 ff  ← 重连后发送正常送达
```

**③ 启动即连** —— HA 重启后自动建立并保持长连接。

**④ 串口模式** —— `/dev/serial/by-id/...` 稳定名打开成功，开关/按钮均实测通过。

> 🔧 开发中修的真问题：① 长连接下对端断开后首条命令静默丢失 → 增加后台监听 + 发送前检查 + 失败重连；
> ② 改地址产生重复实体 → entity unique_id 改用 `entry_id`。

## License

MIT
