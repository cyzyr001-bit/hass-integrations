# Tuya WebGroups

走涂鸦 **Web（SaaS）API** 的灯组集成 —— 只需一个**浏览器 Cookie** 即可接入。

## 为什么用 Web API

涂鸦开放平台的 Group API（`/v2.1/cloud/thing/group/...`）在**组内成员来自不同产品**时，
会丢掉 `bright_value` 字段、并且**静默忽略对它的写入**。
而涂鸦 Web（SaaS）接口是**唯一**能真正对这类混合组调亮度的通道。

## 功能

- 仅需浏览器 Cookie + 微应用 ID，自动发现「家」与其灯组
- 每组一个 `light` 实体，**组级控制**（与涂鸦 App 组状态一致）
- 支持亮度调节

## 安装

```bash
cp -r custom_components/tuya_web_groups /config/custom_components/
```

重启 Home Assistant → 「设置 → 设备与服务 → 添加集成」→ 搜索 **Tuya WebGroups**。

HACS：以「自定义仓库」添加本仓库，类型选 Integration，路径填 `custom_components/tuya_web_groups`。

## 配置

| 字段 | 说明 |
|---|---|
| Cookie | 涂鸦智能网页版的浏览器 Cookie |
| 微应用 ID (micro_app_id) | 默认「群组管理」微应用 `2021044239474884698`，一般无需改 |
| 家庭 (home_id) | 选择要接入的「家」|
| 灯组 / 排除灯组 | 可选，只导入或排除指定灯组 |
| 轮询间隔 (scan_interval) | 默认 15 秒 |

> Cookie 会过期，失效后在集成选项里更新即可。

## 使用的接口（均在 `https://cn.device.tuyasmart.com`）

```
GET  /api/smarthome/user/current                              → 当前用户
GET  /open-api/v2.0/m/sdf/smart-home/projects/sample          → 家庭列表
POST /api/smarthome/home/active                               → 选择家庭
GET  /open-api/v1.0/m/sdf/device-groups                       → 灯组列表
GET  /open-api/v1.0/m/sdf/device-groups/{gid}?product_id=...  → 实时 dps
GET  /open-api/v1.0/m/sdf/device-groups/{gid}/functions?product_id=...
GET  /open-api/v1.0/m/sdf/ss/panels/device/{home}/{dev}/model → abilityId
POST /open-api/v1.0/m/sdf/device-groups/{gid}/control         → 控制
```

必需请求头：`Cookie`、`csrf-token`、`uid`、`micro-app-id`、`project-id`（active home，
缺失会返回「微应用权限错误」）。

## 要求

- Home Assistant ≥ 2023.1.0
- 涂鸦智能账号（网页版可登录）

## License

MIT
