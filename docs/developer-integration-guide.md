# 独立应用接入指南

> **Coding agent：**请先读 [Agent 接入索引](./agent-integration.md) 或仓库根目录 `llms.txt`。

本指南面向独立部署的企业 B 端应用。应用通过版本化 API 与**数据汇聚导出接口**接入平台，不共享平台源码、数据库账号、Cookie 或 Session 表。

## 1. 从身份型接入开始

默认只登记 `API_CLIENT`，无需消息队列或事件基础设施：

1. 在应用中心登记应用并从员工目录选择**应用负责人**；再登记每个环境的入口、严格回调 URI、版本，需要一次性管理员领取时再指定该环境的**初始管理员**。登记操作员、负责人、初始管理员是三个独立角色。
2. 仅选择实际需要的 OAuth scope。应用自己管理业务授权时，用户流只需 `ai_hub.identity`、`platform.me.read`；一次性初始管理员领取另需 `platform.application.bootstrap`；后台同步员工另需 `platform.directory.read`。
3. 创建环境凭据，立即保存只展示一次的 `client_secret`；平台数据库不会保存明文。
4. 用户请求使用授权码 + PKCE；后台任务使用 Client Credentials。两类令牌不可互换。
5. 应用本地验证用户 JWT 的 issuer、audience、签名、有效期和 scope，再调用 `/me` 获取基本身份。OIDC 登录成功本身不授予任何业务权限。
6. 环境中指定的初始管理员首次登录时调用 `admin-bootstrap`；应用在本地幂等创建首个管理员。负责人变化不修改待领取或已领取的初始管理员，也不改写本地管理员。
7. 后台任务按 cursor 调用 `/directory/users` 同步员工资料和状态。所有活动用户（包括平台管理员）均可同步，新用户默认无本地业务角色。平台角色不授予任何业务权限；仅身份停用时返回 `tombstone=true`，应用应禁止其登录并撤销会话，但不要覆盖或删除本地角色历史。身份接口不再提供互斥分类字段 `business_user`；曾按旧规则同步的应用需从空 cursor 完整重同步目录，恢复活动身份资料，但不得自动恢复或新增业务权限。
8. 角色、功能权限、数据范围、对象归属、业务状态和并发规则始终由应用自己存储并最终校验。

上述模式等价于企业内部的 “Sign in with Google”：AI Hub 负责认证和基本资料，业务应用负责全部功能授权。详细边界见 [ADR-034](adr/ADR-034-identity-provider-application-owned-authorization.md)。平台不托管业务授权，不提供 `/me/permissions` 与 `/authorization/decisions`；应用不得在 AI Hub 登记业务权限码。

Python 最小示例见应用中心的接入规范的 `api-only-python` 资产（`examples/sdk/api_only.py`）。平台不提供测试通知服务。

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

消费侧（治理 / AI）使用 `platform.data.read` 查询当前态与历史，见应用中心的接入规范资产 `data-read-python`（`examples/sdk/data_read.py`）。

## 3. 故障与安全边界

- 平台短时不可用时，低风险读操作只可使用版本一致且有界的授权快照；高风险写操作失败关闭。
- authentik 短时不可用时，本地 JWKS 缓存只在配置的陈旧窗口内继续验证已签发令牌。
- 凭据轮换后立即替换应用密钥并清除令牌缓存；吊销后平台服务主体绑定会立即拒绝已签发令牌。
- 传播 `X-Request-ID` 和 `X-Trace-ID`，排障时使用审计中心关联，不在日志中记录令牌、Cookie 或密钥。
- 每个环境分别登记回调、scope、凭据与数据库角色；生产与非生产不共享凭据或数据。

## 4. 一致性认证

提交上线前在应用详情的「接入检查」中运行与架构一致的认证配置：只借用登录和员工目录的应用运行 `OIDC_ONLY`；使用平台服务身份和公共 API 的应用运行 `API_ONLY`；启用数据汇聚时运行 `DATA_INGEST`。未启用能力应显示为「不适用」，不能为了通过认证而安装无用基础设施。

`DATA_INGEST` 运行时证据须证明：导出接口可达且校验 `ai_hub.ingest.export`、version 全序单调、回看窗口下无漏拉、删除可捕获、幂等正确、payload 符合已登记契约。本地门禁通过后，可参考应用中心的接入规范资产 `data-ingest-evidence`（`examples/sdk/data_ingest_evidence.py`）生成并导入证据摘要。

Pull 数据源须先登记、人工审核并激活契约，再启用严格同步并收集认证证据；不要求首次同步前已有认证记录。Push 数据源仍须先通过隔离认证才可启用。
