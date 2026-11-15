# AI Hub Python SDK

> 2026-10-16 简化基线：平台保留统一身份、应用登记与凭据、数据接入、审计和运维。平台托管业务授权及测试通知已退役，相关旧接口、SDK 方法和菜单不再提供；业务授权全部由应用执行。初始管理员领取与员工目录同步为可选能力。数据接入仅支持 ENFORCE，已移除推断草稿与 AUDIT_ONLY。本文后续涉及旧功能或阶段性兼容的内容仅作历史记录，当前接入以 开发者指南 `docs/developer-integration-guide.md` 和 API 契约为准。

> Agent 接入索引：[docs/agent-integration.md](../../docs/agent-integration.md)

SDK 只封装公开协议，不包含平台业务实现或平台数据库访问。

当前版本包括：

- 异步平台 API 客户端：健康、当前用户、应用登记、管理员领取、员工目录与汇聚数据读取。
- OIDC 授权码 + PKCE、Client Credentials、Discovery/JWKS 本地验证。
- `DATA_INGEST` 导出契约辅助：导出页构建、版本单调校验、payload 白名单与导出 scope 检查。

API-only 最小示例由应用中心接入规范的 `api-only-python` 资产提供（`examples/sdk/api_only.py`）。它只依赖公开 SDK、OIDC 和平台 API。

读取汇聚数据见 `data-read-python`（`examples/sdk/data_read.py`）。

启用 `DATA_INGEST` 时，应用自行实现导出端点与变更日志；SDK 提供契约辅助函数，不隐式创建应用侧持久化对象。本地导出门禁通过后，见 `data-ingest-evidence`（`examples/sdk/data_ingest_evidence.py`）生成认证证据摘要。
