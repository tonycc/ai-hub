# AI Hub Python SDK

SDK 只封装公开协议，不包含平台业务实现或平台数据库访问。

当前版本包括：

- 异步平台 API 客户端：健康、当前用户、权限快照、在线决策、应用登记和通知。
- OIDC 授权码 + PKCE、Client Credentials、Discovery/JWKS 本地验证。
- 按主体、应用和授权版本隔离的有界授权缓存。
- CloudEvents 事件信封、业务中性快照与校验和。

API-only 最小示例由开发者中心的 `api-only-python` 资产提供。它只依赖公开 SDK、OIDC 和平台 API，不需要 Outbox、Inbox 或 RabbitMQ。

Outbox/Inbox 属于应用自己的可选持久化能力，不由 SDK 隐式创建。只有启用可靠事件发布或会产生本地持久化副作用的事件消费时才安装对应模板和 Worker。
