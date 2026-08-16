# AI Hub Python SDK

SDK 只封装公开协议，不包含平台业务实现或平台数据库访问。

当前版本包括：

- 异步平台 API 客户端：健康、当前用户、权限快照、在线决策、应用登记和通知。
- OIDC 授权码 + PKCE、Client Credentials、Discovery/JWKS 本地验证。
- 按主体、应用和授权版本隔离的有界授权缓存。
- `DATA_INGEST` 导出契约辅助：导出页构建、版本单调校验、payload 白名单与导出 scope 检查。

API-only 最小示例由开发者中心的 `api-only-python` 资产提供。它只依赖公开 SDK、OIDC 和平台 API。

启用 `DATA_INGEST` 时，应用自行实现导出端点与变更日志；SDK 提供契约辅助函数，不隐式创建应用侧持久化对象。
