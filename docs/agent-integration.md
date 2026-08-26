# Agent 接入索引

面向 coding agent / LLM 的**固定阅读顺序**与机器可读入口。人类开发者请同时阅读 [独立应用接入指南](./developer-integration-guide.md)。

## 1. 先决条件（人工步骤）

以下步骤**无法**仅通过 Platform API 完成，需要运维或门户操作：

1. 在**应用中心**登记应用、环境 URL、OAuth 回调 URI、所需 scope。
2. 创建环境凭据并**一次性保存** `client_secret`（平台不存明文）。
3. 上线前在**接入治理**运行 `API_ONLY` / `DATA_INGEST` 认证（若已启用对应能力）。

Agent 应生成应用侧代码与配置模板，并明确列出需人工填写的密钥与登记项。

## 2. 场景决策树

```
需要把业务对象同步到平台供治理/AI 消费？
├─ 否 → 能力 API_CLIENT
│        读：integration-guide §1 + platform-openapi + api-only-python
│        实现：OIDC（用户 PKCE / 服务 Client Credentials）→ 验证 JWT → 调 Platform API
└─ 是 → 能力 API_CLIENT + DATA_INGEST
         读：integration-guide §2 + data-ingest-evidence + SDK export 模块
         实现：应用侧 GET /ai-hub/export（见 §4）+ 可选 platform.data.read 消费示例
```

## 3. 推荐阅读顺序

| 顺序 | 资产 ID | 仓库路径 | 用途 |
| --- | --- | --- | --- |
| 1 | `agent-integration` | `docs/agent-integration.md` | 本索引 |
| 2 | `integration-guide` | `docs/developer-integration-guide.md` | 接入流程、安全边界、认证 |
| 3 | `platform-openapi` | `contracts/api/platform-api.openapi.yaml` | 平台公开 API 契约 |
| 4 | `api-only-python` | `examples/sdk/api_only.py` | 最小连通性：health + 通知 |
| 5 | `data-read-python` | `examples/sdk/data_read.py` | 读取汇聚数据（消费方） |
| 6 | `data-ingest-evidence` | `examples/sdk/data_ingest_evidence.py` | DATA_INGEST 认证证据模板 |

运行时拉取（需门户会话或等价认证）：

```
GET {PLATFORM_BASE_URL}/portal-api/v1/developer/assets/{asset_id}
```

资产目录：

```
GET {PLATFORM_BASE_URL}/portal-api/v1/developer/catalog
```

每条资产含 `sha256`，下载后应校验完整性。

## 4. 环境变量（Python SDK 示例）

| 变量 | 说明 |
| --- | --- |
| `AI_HUB_PLATFORM_URL` | 平台 base URL |
| `AI_HUB_OIDC_ISSUER` | authentik issuer |
| `AI_HUB_CLIENT_ID` | 应用 client id |
| `AI_HUB_CLIENT_SECRET` | 应用 client secret（仅服务身份示例） |
| `AI_HUB_APPLICATION_ID` | 登记的应用 slug |
| `AI_HUB_RECIPIENT_USER_ID` | `api-only-python` 通知收件人 uuid |

门户开发者中心沙箱片段提供 `PLATFORM_BASE_URL`、`OIDC_ISSUER` 等占位值。

## 5. OAuth 与 scope 要点

- **用户请求**：授权码 + PKCE；应用本地验证 JWT（issuer、audience、签名、exp、scope）后再调 `/me`。
- **后台任务**：Client Credentials；与用户令牌**不可互换**。
- **所有受保护 Platform API** 均需 scope `ai_hub.identity`，外加各接口声明的 scope（见 OpenAPI `security`）。
- **ingest 导出**（应用侧）：调用方须带 scope `ai_hub.ingest.export`（平台服务身份）。

常用 Platform scope：

| Scope | 用途 |
| --- | --- |
| `platform.me.read` | `/me`、`/me/permissions` |
| `platform.authorization.decide` | 在线授权决策 |
| `platform.application.read` | 读取应用登记 |
| `platform.application.health.write` | 上报环境健康 |
| `platform.notification.request` | 创建/查询通知 |
| `platform.data.read` | 查询汇聚对象 |

## 6. 应用侧导出契约（不在 platform-openapi 内）

集成应用若启用 `DATA_INGEST`，须实现：

```
GET {api_base_url}/ai-hub/export?object_type={type}&since_version={n}&limit={n}
Authorization: Bearer <platform service token>
```

响应 JSON 信封（与 SDK `ExportPage` 一致）：

```json
{
  "object_type": "string",
  "payload_contract_version": "string",
  "records": [
    {
      "object_id": "string",
      "operation": "upsert | delete",
      "version": 1,
      "payload": {} 
    }
  ],
  "has_more": false,
  "high_watermark": 0
}
```

规则摘要：

- `delete` 时 `payload` 必须为 `null`；`upsert` 时 `payload` 必填。
- 同一 `(application, object_type)` 下 `version` **全序单调**，与事务提交顺序一致。
- 删除须显式 `operation=delete`，不能仅靠对象从后续增量中消失。
- 须校验调用方 scope 为 `ai_hub.ingest.export`（SDK：`require_export_scope`）。

参考实现：`examples/standalone-app/src/standalone_app/main.py` 中 `GET /ai-hub/export`；模型定义：`sdk/python/src/ai_hub_sdk/export.py`。

## 7. API-only 最小检查清单

- [ ] 应用已在应用中心登记，回调 URI 与环境匹配。
- [ ] 凭据与 scope 已配置；`client_secret` 已安全存储。
- [ ] 用户流：PKCE 换 token → 本地 JWT 验证 → `GET /platform-api/v1/me`。
- [ ] 服务流：Client Credentials → `GET /health/live` → 业务 API（如通知带 `idempotency_key`）。
- [ ] 错误处理：解析 `ErrorResponse.error_code`，记录 `request_id`，不记录 token/secret。
- [ ] 请求头：按需传 `X-Application-ID`；传播 `X-Request-ID` / `X-Trace-ID`。

## 8. DATA_INGEST 额外检查清单

- [ ] 应用 capability 含 `DATA_INGEST`；对象类型与 payload 契约已登记。
- [ ] 实现 `/ai-hub/export` 并校验 `ai_hub.ingest.export`。
- [ ] version 单调、删除可捕获、payload 契约化（非裸表行）。
- [ ] 接入治理运行 `DATA_INGEST` 认证；可用 `data-ingest-evidence` 生成证据摘要。

## 9. 仍超出 agent 自动化范围

- 门户 UI：应用登记、凭据发放、接入治理认证触发。
- 生产密钥与 authentik 配置变更。
- 跨环境凭据共享（**禁止**）。

完成代码生成后，应输出：待人工执行的门户步骤、所需 scope 列表、以及建议运行的示例命令（`api_only.py` 等）。
