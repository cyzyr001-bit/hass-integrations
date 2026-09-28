# Tuya Cloud Groups

把 **涂鸦云灯组** 同步进 Home Assistant 的集成 —— 走涂鸦**开放平台 OpenAPI**。

填写涂鸦云 Access ID / Access Secret，**自动导入该"家"（space）下的所有灯组**并实时控制。

## 功能

- 按账号家庭（space）自动同步灯组，每组一个 `light` 实体
- **组级控制**：所有控制都作用在涂鸦灯组上，与手机 App 看到的组状态保持一致
- 支持亮度调节
- 可指定/排除灯组（group_ids / group_names / excluded_groups）

## 安装

```bash
cp -r custom_components/tuya_cloud_groups /config/custom_components/
```

重启 Home Assistant → 「设置 → 设备与服务 → 添加集成」→ 搜索 **Tuya Cloud Groups**。

HACS：以「自定义仓库」添加本仓库，类型选 Integration，路径填 `custom_components/tuya_cloud_groups`。

## 配置

| 字段 | 说明 |
|---|---|
| Access ID (client_id) | 涂鸦开放平台 Access ID |
| Access Secret (client_secret) | 涂鸦开放平台 Access Secret |
| 用户 ID (user_id) | 可选，限定某用户 |
| 区域 (region) | 数据中心区域（默认 `cn`，可选 eu / us 等）|
| 家庭 (space_id) | 涂鸦「家」的 ID |
| 灯组 (group_ids / group_names) | 可选，只导入指定灯组 |
| 排除灯组 (excluded_groups) | 可选，手动移除后不再导入 |
| 轮询间隔 (scan_interval) | 同步间隔（秒）|

## 使用的接口（实测 cn 区域）

```
GET /v2.1/cloud/thing/group                          → 灯组列表
GET /v2.1/cloud/thing/group/{group_id}               → 灯组详情
GET /v2.1/cloud/thing/group/{group_id}/devices       → 组内设备
GET /v1.0/devices/{device_id}                        → 单个设备（名称/状态）
GET /v2.1/cloud/thing/group/{gid}/properties         → 组属性规格
GET /v2.0/cloud/thing/group/{gid}/properties         → 实时值（含 bright_value）
```

## 说明

- 仅做**组级**控制（不做单设备控制），因此与涂鸦 App 的组状态始终同步。
- 依赖涂鸦开放平台，需要先在涂鸦 IoT 平台创建应用并授权。

## 要求

- Home Assistant ≥ 2023.1.0
- 涂鸦开放平台账号 + 已授权的应用（Access ID / Secret）

## License

MIT
