# 独立应用接入指南

> **Coding agent：**请先读 [Agent 接入索引](./agent-integration.md) 或仓库根目录 `llms.txt`。

本指南面向独立部署的企业 B 端应用。应用通过版本化 API 与**数据汇聚导出接口**接入平台，不共享平台源码、数据库账号、Cookie 或 Session 表。

## 1. 从 API-only 开始

默认只登记 `API_CLIENT`，无需消息队列或事件基础设施：

1. 在应用中心登记应用和每个环境的入口、严格回调 URI 与版本。
2. 仅选择实际需要的 OAuth scope。
3. 创建环境凭据，立即保存只展示一次的 `client_secret`；平台数据库不会保存明文。
4. 用户请求使用授权码 + PKCE；后台任务使用 Client Credentials。两类令牌不可互换。
5. 应用本地验证用户 JWT 的 issuer、audience、签名、有效期和 scope，再调用 `/me` 与权限 API。
6. 对象归属、业务状态和并发规则始终由应用自己最终校验。

Python 最小示例见开发者中心的 `api-only-python` 资产（`examples/sdk/api_only.py`）。服务通知请求必须携带唯一幂等键；失败需按公开错误码处理，不得静默丢弃。

## 2. 数据汇聚（推荐）

需要把业务对象同步到平台供治理 / AI 消费时，登记能力 `DATA_INGEST`，实现增量导出接口。平台按位点拉取，应用无需维护消息投递基础设施。

### 2.1 导出接口

```
GET {app_base_url}/ai-hub/export?object_type={type}&since_version={n}&limit={n}
```

- 认证：平台服务身份 Bearer 令牌。
- 授权：必须校验专用 scope `ai_hub.ingest.export`（不能只校验“令牌合法”）。
- 响应信封固定：`object_type`、`payload_contract_version`、`records[]`、`has_more`、`high_watermark`。
- 每条记录：`object_id`、`operation`（`upsert` | `delete`）、`version`、`payload`（delete 时为 `null`）。

Python 辅助见 SDK：`ExportPage` / `ExportRecord` / `paginate_export_records` / `require_export_scope` / `PayloadContract`。参考应用示例：`GET /ai-hub/export`（`example_record`）。

### 2.2 硬要求

1. **`version` 在 (应用, 对象类型) 全序单调**，且与**事务提交顺序**一致（或接受平台安全回看窗口）。
2. **删除必须显式上报**（软删变更日志 / `operation=delete`），不能只靠“下次全量里消失”。
3. 按 `since_version` 增量查询，结果按 `version` 有序。
4. 同一对象同一版本只出现一次。
5. **payload 契约化**：字段经过筛选/脱敏并登记 `payload_contract_version`，禁止直接 dump 表行。

### 2.3 首次接入

1. 登记应用 + 对象类型 + payload 契约，初始化位点 `last_version = 0`。
2. 平台跑一次 `full` 建基线（对缺席对象合成删除墓碑）。
3. 转入 `incremental`，按周期拉取；失败不推进位点，下次重拉（幂等去重）。

消费侧（治理 / AI）使用 `platform.data.read` 查询当前态与历史，见开发者中心资产 `data-read-python`（`examples/sdk/data_read.py`）。

## 3. 故障与安全边界

- 平台短时不可用时，低风险读操作只可使用版本一致且有界的授权快照；高风险写操作失败关闭。
- authentik 短时不可用时，本地 JWKS 缓存只在配置的陈旧窗口内继续验证已签发令牌。
- 凭据轮换后立即替换应用密钥并清除令牌缓存；吊销后平台服务主体绑定会立即拒绝已签发令牌。
- 传播 `X-Request-ID` 和 `X-Trace-ID`，排障时使用审计中心关联，不在日志中记录令牌、Cookie 或密钥。
- 每个环境分别登记回调、scope、凭据与数据库角色；生产与非生产不共享凭据或数据。

## 4. 一致性认证

提交上线前在「接入治理」页面分别运行已启用能力的认证配置：`API_ONLY`、`DATA_INGEST`。未启用能力应显示为「不适用」，不能为了通过认证而安装无用基础设施。

`DATA_INGEST` 运行时证据须证明：导出接口可达且校验 `ai_hub.ingest.export`、version 全序单调、回看窗口下无漏拉、删除可捕获、幂等正确、payload 符合已登记契约。本地门禁通过后，可参考开发者中心资产 `data-ingest-evidence`（`examples/sdk/data_ingest_evidence.py`）生成并导入证据摘要。
