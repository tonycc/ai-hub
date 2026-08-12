# AI Hub Python SDK

SDK 只封装公开协议，不包含平台业务实现或平台数据库访问。

当前骨架包括：

- 异步平台 API 客户端。
- 健康检查契约。
- CloudEvents 事件信封。

Outbox/Inbox 的数据库表和事务模板会作为独立的可选 extra 提供，API-only 应用不需要安装或启用。

权限决策客户端将在 authentik JWT 验证和平台权限 API 同时落地时加入，当前 SDK 不暴露尚未实现的远程方法。
