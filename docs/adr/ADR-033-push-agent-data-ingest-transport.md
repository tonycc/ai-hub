# ADR-033：Push 作为 DATA_INGEST 的第二种数据面传输

- 状态：已接受
- 决策日期：2026-08-29
- 接受日期：2026-08-29
- 决策范围：应用/中间机向平台汇聚业务对象时，是否允许入站推送承载权威数据；以及与 ADR-032、增量汇聚设计 §8.6 的关系
- 详细方案：[`../data2agent-core-copy-upgrade-implementation-plan.md`](../data2agent-core-copy-upgrade-implementation-plan.md)

## 背景

ADR-032 已决定：用贴源层 `platform_raw` + 定期增量汇聚替代 M2 实时事件投影。该决策的核心正确性约束仍然成立：版本化 payload 契约、统一 Raw 四表、幂等、full 墓碑、禁止无契约 dump、平台不直连源库。

增量汇聚设计 §8 第 6 项随后冻结为：**应用主动推送首期不做**；若做，也只允许轻量「有变更」提示，平台立刻去拉，**推送不承载业务数据**。

工厂侧 ERP/MES 不能或不适合对平台暴露导出接口。data2agent 中间机已具备只读抽取、白名单、增量/全量和 Push 协议。若坚持「权威数据只能拉」，中间机必须再实现一套 Pull 导出，或平台反向连接工厂网络；两者都破坏现有边界。需要单独决定：能否在不新建第二套 Raw、不绕过契约的前提下，让 Push 写入同一 `DATA_INGEST` 数据面。

## 决策

**接受 `PUSH_AGENT` 作为现有 `DATA_INGEST` 能力的第二种传输模式，允许入站推送承载已登记契约的业务对象记录，并写入现有 `platform_raw`。** 本 ADR **替代**增量汇聚设计 §8 第 6 项；不得以「补充 ADR-032」规避这次变更。

ADR-032 下列决策继续有效，不在此重开：统一 Raw 四表、payload 契约化、幂等与 version 全序、full「缺席即权威」墓碑、禁止无契约 dump、平台不直连源数据库、纯 HTTP、不恢复 M2。

约束如下：

1. **单一能力、两种传输。** 不新增 `RAW_PUSH_INGEST`。`(source_application_id, object_type)` 仍只有一条 `ingest_source`，`transport_mode` 为 `PULL_EXPORT` 或 `PUSH_AGENT`，互斥。调度器只加载 Pull；Push API 只接受已登记为 Push 的来源。
2. **统一契约与 Ingest Core。** Pull 与 Push 共用 `platform_core.ingest_contract` 和 Validator，再进入现有 `load_batch` / 墓碑语义。契约能力不是 Push 私有扩展。存量 Pull 先 `AUDIT_ONLY`，按来源认证后再 `ENFORCE`；Push 从第一天起 `ENFORCE`。
3. **Push 承载规范化对象记录，不承载物理表 dump。** 中间机把源表映射为 `object_type` / `object_id` / `version` / `payload`。平台不持有抽取位点，不反向连接中间机或 ERP/MES。
4. **全量不得按分页调用 `load_batch(full)`。** Push 多批次 full 先 staging，仅 `complete` 时发布一次并合成墓碑。Pull full 仍由调度器收齐分页后调用一次 `load_batch(full)`；`ENFORCE` 下任一页契约失败则整次 full 失败，不得带着残页去合成墓碑。
5. **重建分流。** 两种传输均可从 `raw_change_record` 重放当前态。`rebuild_from_source` 仅适用于 Pull。Push 的源重建由中间机发起新的 full generation；平台对该动作返回 `409 source_rebuild_not_supported`。
6. **身份。** 中间机使用平台签发的 OIDC Client Credentials；audience `ai-hub-platform`，scope 含 `ai_hub.ingest.push`。可写来源由令牌声明和 `ingest_source` 决定，禁止靠请求体冒充其他来源。
7. **启用门禁。** 功能开关 `DATA_INGEST_PUSH_ENABLED` 默认关闭。关闭时现有 Pull 行为与升级前一致。生产启用还须通过平台接收端、参考中间机和跨仓兼容三道门禁。

语义中心、模型构建和 MCP 不在本 ADR 范围内，不得作为启用 Push 的前置条件。

## 备选方案（未选择）

### 维持 §8.6：Push 只作拉取加速信号

未选择。工厂侧没有可被平台拉取的导出接口时，提示 webhook 无处可拉；中间机还要再实现 Pull 导出，等于拒绝当前接入路径。

### 新建平行的 RAW_PUSH_INGEST 与第二套 Raw

未选择。会分裂契约、对账、保留、重建和运维，正是 ADR-032 要避免的双轨。

### 平台直连 ERP/MES 或订阅 CDC

未选择。违反「平台不得直连源库」和「禁止无契约 dump」。

## 后果与风险控制

| 后果或风险 | 控制措施 |
| --- | --- |
| 入站推送扩大攻击面 | OIDC 短时令牌、来源绑定、契约 ENFORCE、批次摘要冲突、审计；请求体不能单独决定可写来源 |
| 分页 full 误删其他页对象 | 禁止按页 `load_batch(full)`；Push staging + 一次 complete；Pull ENFORCE 残页则整次失败 |
| 存量 Pull 被新契约误拦截 | 回填 `AUDIT_ONLY` 且永不因契约拒绝；按来源认证后才 ENFORCE，可即时退回 |
| 对 Push 误执行平台源重建 | API/CLI/门户按 transport 拒绝；源重建由中间机新 full generation |
| 与 ADR-032「平台拉取」表述冲突 | 本 ADR 显式扩展传输方向；拉取仍是默认路径，Push 是登记后的第二种路径 |

## 复核机制

- 生产环境打开 `DATA_INGEST_PUSH_ENABLED` 前，必须完成实施方案立项 A 的 C1-A / C1-B / C1-C 门禁，并按来源启用。
- 关闭 Push 开关时，现有 Pull 调度、Raw 四表、对账和平台源重建必须继续工作。
- 若未来要恢复「推送仅提示、权威数据只拉」，须新开 ADR 替代本决策，不能静默退回 §8.6 原文。

## 关联

- [ADR-032：以定期增量数据汇聚替代 M2 实时事件投影](ADR-032-incremental-ingestion-replaces-m2.md)
- [增量数据汇聚与统一治理设计与实施方案](../incremental-data-ingestion-design.md)（§8.6 由本 ADR 替代）
- [data2agent 核心能力复制升级实施方案](../data2agent-core-copy-upgrade-implementation-plan.md)
