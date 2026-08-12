# Public contracts

这里保存平台对独立应用公开的事实契约：

- `api/`：OpenAPI 契约。
- `events/`：AsyncAPI 和 CloudEvents JSON Schema。

Python SDK 由这些契约实现并接受契约测试，不能成为替代契约本身的新事实来源。破坏性变更创建新版本，并保留明确兼容窗口。

M0-09 的基础契约门禁会校验 OpenAPI 版本、唯一 `operationId` 与本地引用，校验 AsyncAPI 指向存在且有效的 Draft 2020-12 CloudEvents Schema，并用 Python SDK 生成的事件实例验证 Schema 一致性。这些是结构和最小消费方契约，不替代 M1/M2 实现后的鉴权、兼容窗口、提供方和事件处理行为测试。
