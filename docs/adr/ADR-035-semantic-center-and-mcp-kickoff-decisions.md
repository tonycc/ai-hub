# ADR-035：立项 B（语义中心与只读 MCP）开工决策

- 状态：已接受
- 决策日期：2026-11-10
- 接受日期：2026-11-10
- 决策范围：[data2agent 核心能力复制升级实施方案](../data2agent-core-copy-upgrade-implementation-plan.md) 的 C0-B 六项开工决策，以及该方案与 2026-10-16 简化基线之间需要重新裁决的两处冲突

## 背景

AI Hub 的定位确定为**数据型 MCP 平台**：ERP、MES、SRM 等系统的数据经现有 `DATA_INGEST` 汇聚到 Raw，经语义层统一口径后，由只读 MCP 服务供 Agent 做跨系统关联和历史统计。实时查询和写操作不在本立项范围内。

实施方案已把这条链路拆成立项 B 的 C2（本体定义）、C3（模型构建）、C4（MCP 服务）、C5（试点），并要求在 C2 或 C4 开工前完成 C0-B 的六项决策。本 ADR 逐项给出决策。

方案文本冻结于 2026-08-31（V0.4.2）。此后有两件事改变了前提，必须在开工前处理：

1. **平台权限模块已退役。** 2026-10-16 简化基线删除了平台托管的业务授权（[ADR-034](ADR-034-identity-provider-application-owned-authorization.md)）。方案 §6.3、§6.4 中"对象和指标读取继续调用平台权限决策""按组织/工厂范围计算"已无实现可依。
2. **data2agent 主干已移除平台端。** 该仓库当前 `main`（`f97af27`）在提交 `ecc7b823` 之后只保留中间机，方案 §2.1 点名复用的元模型、证据和 MCP 代码在主干上已不存在，只存在于锁定提交 `60bcbe2`（[集成锁定清单](../../deploy/integration-lock.json)）。

## 决策

### 1. 元模型、生命周期和模型表策略

按方案 §5.2、§5.4、§5.5 现有文本冻结，不做修改：

- 元模型为 Object、Property、Relation、Metric、SourceBinding 五类。
- 生命周期为 `DRAFT → REVIEWED → VERIFIED → PUBLISHED → DEPRECATED → RETIRED`，自动生成最高到 `DRAFT`。
- 模型层使用 `model.object_row`、`model.relation_row`、`model.metric_value` 三张固定表加 `dataset_version`，由 Alembic 建表，构建器运行时不执行 DDL；发布和回滚只切换已发布数据集指针。

### 2. 平台侧 AdapterMapping 后置

首个语义切片**不实现**平台侧 AdapterMapping。SourceBinding 只引用共用 `ingest_contract` 的契约版本和 Schema 指纹，属性到 payload 字段使用受限的直接映射（字段路径、类型转换、枚举值映射）。

理由：首批对象的 payload 契约已经过白名单和脱敏，字段与业务属性基本一一对应；物理表和列的映射留在中间机受限配置里，平台不需要再存一份。出现一个语义对象需要合并多个契约对象的需求时，再单独评审引入。

### 3. 语义门户最小页面

首期门户只做三个页面，全部挂在现有平台管理端下：

| 页面 | 能力 |
| --- | --- |
| 语义包列表与版本详情 | 查看对象、属性、关系、指标和绑定；查看校验结果 |
| 审核与发布 | 从契约生成 `DRAFT`、技术评审、负责人确认、发布 |
| 数据集 | 触发构建、查看隔离统计、发布、回滚到直接前一版本 |

首期不做在线编辑器：语义包内容以 YAML/JSON 导入和导出，修改即导入新版本。门户写操作沿用现有门户权限、CSRF 和审计机制，权限码使用方案 §5.6 列出的 `platform.semantic.*` 和 `platform.model.*`。

### 4. 消费面与数据范围

**消费面**按方案 §6.1 冻结：新 Agent 一律使用 MCP；`/platform-api/v1/data` 保留为内部 Raw 兼容和治理诊断接口，不再作为 Agent 的推荐接入面；`ai_hub_mcp` 数据库角色不得读取 `platform_raw`。

**数据范围**替换方案 §6.3、§6.4 的相关表述，改为**按来源应用授权**：

- 新增授权表（暂名 `semantic.mcp_access_grant`），记录"哪个调用主体可以读哪个来源应用的哪些已发布语义对象"。主体是 Authentik 中的服务身份或用户；对象一栏可为"该来源应用的全部已发布对象"。
- MCP 的四个工具在查询前按授权表过滤：未授权的对象不出现在 `discover_objects` 结果中，直接查询返回与"对象不存在"相同的错误，不泄露存在性。
- 没有任何授权记录的主体看不到任何对象（默认拒绝）。
- 授权由平台运营人员在门户维护，写操作进审计。
- **首期不做行级过滤**（组织、工厂、数据归属）。

这与 ADR-034 不冲突：ADR-034 约束的是平台不替业务应用判定其自身功能的权限；这里控制的是平台自己持有的汇聚数据由谁读取，属于平台自身资源的访问控制。

后果：同一来源应用内的数据对被授权主体全部可见，仅受字段脱敏保护。需要按工厂或组织隔离的数据，在行级过滤能力就绪前不应发布到 MCP。

### 5. MCP v1 工具、身份、限流和证据

按方案 §6.2 至 §6.5 冻结，除上面第 4 项的数据范围外不做修改：

- 工具：`discover_objects`、`describe_object`、`query_objects`、`query_metrics`，全部只读。
- 身份：Authentik OIDC Bearer Token，audience 固定为 `ai-hub-mcp`，最小 scope 为 `platform.mcp.invoke`。
- 查询：单次 `limit` 默认 100、最大 200；筛选、排序、分组只能来自元模型白名单；敏感属性默认返回 `***`，首期不提供解敏开关。
- 限流：每主体、每工具限流，配置进入生产目标。

**证据写入路径**（方案留待本决策）：查询证据**直写 `semantic` 库的专用证据表**，`ai_hub_mcp` 角色对该表只有 `INSERT` 和按主体查询自己证据的权限；同时向现有平台审计追加一条不含结果内容的调用摘要。

理由：证据带 `result_digest`、`dataset_version`、`binding_versions` 等结构化字段，需要按查询回溯，放进通用审计事件的 `metadata` 里不便检索；MCP 是独立进程，直写专表也避免让每次查询同步依赖平台 API。平台审计仍保留"谁在何时调了什么"的统一视图。

### 6. data2agent 代码复用

复用来源固定为锁定提交 `60bcbe22f0db6457d81d24a0d9924723e085cdae`（2026-08-31），不跟随该仓库主干。许可为 Apache-2.0，仓库带 `NOTICE`。

| 来源文件（锁定提交） | 行数 | 处理 | 进入阶段 |
| --- | --- | --- | --- |
| `data2agent/shared/metamodel/schema.py` | 196 | 直接复制后扩展。只依赖 pydantic | C2 |
| `data2agent/shared/metamodel/versioning.py` | 132 | 直接复制。只依赖 pydantic 和标准库 | C2 |
| `data2agent/shared/metamodel/loader.py`、`validate.py` | 90、34 | 复制加载和跨引用校验逻辑；去掉文件路径和命令行入口 | C2 |
| `data2agent/shared/store/evidence.py` | 366 | 只复制规范化 JSON、摘要和结果汇总等纯函数；`EvidenceStore` 依赖 `LandingStore`，按 PostgreSQL 重写 | C4 |
| `data2agent/shared/mapping.py`、`store/mapping_transform.py` | 413、545 | 行为参考：只移植受限类型转换和值映射规则 | C3 |
| `data2agent/shared/store/field_lineage.py`、`dataset_publish.py`、`mapping_preview.py` | 582、1272、1416 | 行为参考，全部按 PostgreSQL 重写，不复制代码 | C3 |
| `data2agent/platform/mcp_server/core.py` | 1128 | 行为参考：工具契约、白名单查询、失败关闭；服务重写 | C4 |
| `templates/objects/*.yaml`、`templates/metrics/core.yaml` | — | 仅导入通用对象（`material`、`customer`、`sales_order`、`sales_order_line`、`quotation`）为 `DRAFT` 种子 | C2 |

不导入 `dead_stock`、`ecn_change`、`purchase_overbuy` 等专项场景模板，方案 §2.2 已将其排除在平台核心之外。

复制动作按方案 §2.3 执行：新增 `THIRD_PARTY_NOTICES.md` 和复制清单，记录来源仓库、提交、路径、SHA-256 和修改摘要；原样复制用一个独立提交，适配改造用后续提交。

后果：data2agent 主干后续对这些文件不会再有更新，复制后由 AI Hub 自行维护，不存在上游同步问题。

## 首个演示切片

沿用立项 A 已验收的三个契约对象，不新增来源：

```text
来源：standalone-example（data2agent E10-like 中间机，PUSH_AGENT）
契约对象：erp.item、erp.sales_order、erp.sales_order_line
语义对象：material、sales_order、sales_order_line
关系：sales_order → sales_order_line → material
演示问题：按物料统计销售订单行数量；查询某物料关联的订单
```

真正的跨系统场景（ERP 与 MES、SRM 关联）需要第二个来源接入后才能验证，放在 C5 试点，不作为 C2 至 C4 的退出条件。

## 备选方案（未选择）

### 恢复平台权限模块以支持组织/工厂行过滤

未选择。等于撤销 10 月的简化决定，并让平台重新理解各应用的组织语义。首期用来源应用粒度的授权加字段脱敏；行级过滤作为后续独立能力评审。

### MCP 直接读取 Raw，跳过语义层和模型层

未选择。Raw 没有统一口径和主键对应关系，同一问题会得到不一致的答案；敏感字段未经属性级分类。方案 §1.3 第 6 条已明确禁止。

### 证据只写平台审计

未选择。见决策 5 的理由。

### 跟随 data2agent 主干或以依赖包形式引用

未选择。主干已移除相关代码；以包形式依赖锁定提交会把整个平台端代码（含 SQLite 存储）带进来，而实际复用的只有约 450 行纯模型和纯函数。

## 后果与风险控制

| 后果或风险 | 控制措施 |
| --- | --- |
| 来源应用内无行级隔离 | 需隔离的数据暂不发布到 MCP；发布评审清单增加"是否含需按组织隔离的数据"一项 |
| 授权表配置错误导致越权读取 | 默认拒绝；授权变更进审计；`discover_objects` 结果即授权的可见性自检 |
| 首期没有在线编辑器，修改语义包需重新导入 | 导入即校验并生成新版本，已发布版本不可变；编辑器按使用反馈后置 |
| 复制代码与 AI Hub 类型检查基线不一致（pyright strict、Python 3.14） | 适配提交中修正；复制清单记录每个文件的修改摘要 |
| 演示切片只有一个来源，不能证明跨系统关联 | C5 试点前接入第二个来源；容量和关联结论只覆盖已测试范围 |

## 实施记录（C2，2026-11-10）

实施时对本 ADR 和实施方案做了三处具体化，记录在此以便追溯：

1. **`semantic` 并入 core 迁移链。** Schema 独立，但由 `ai_hub_platform_migrator` 通过 core 链创建和迁移（`20261110_core_0030`），不新建第三条迁移链。
2. **C2 不新增数据库登录角色。** 实施方案 §3.2 的表格把 `semantic` 的写入角色写作 `ai_hub_semantic`，但 C2 唯一的写入方是平台 API。新增登录角色要求每个已部署环境先由超级用户手工创建角色并下发新密钥，迁移才能执行；因此 C2 改为给现有 `ai_hub_platform` 显式授予 `SELECT, INSERT, UPDATE`（无 `DELETE`），`ai_hub_raw` 无任何权限。最小权限由三层保证：表级授权、内容与已发布快照不可变的触发器、同一语义包至多一个 `PUBLISHED` 的唯一索引，并纳入 `role-boundaries.sql` 校验。独立进程的角色 `ai_hub_model_builder`（C3）和 `ai_hub_mcp`（C4）仍按方案在各自阶段新增。
3. **新增权限码 `platform.semantic.verify`。** 方案 §5.6 列出 read、write、review、publish 四个权限码，没有覆盖数据负责人确认这一步。为使评审、确认、发布可以分配给不同角色，增加 verify。默认授权：平台管理员拥有全部五个；平台运营拥有 read、write、review；安全审计只有 read。

## 接受后的下一步

1. 更新实施方案：把 §6.3、§6.4 中依赖平台权限决策的表述改为引用本 ADR，记录 C0-B 完成。
2. 开始 C2：创建 `semantic` Schema 与角色、复制元模型（独立提交）、实现从 `ingest_contract` 生成 `DRAFT`。

## 关联

- [data2agent 核心能力复制升级实施方案](../data2agent-core-copy-upgrade-implementation-plan.md)
- [ADR-032：增量汇聚替代 M2](ADR-032-incremental-ingestion-replaces-m2.md)
- [ADR-033：PUSH_AGENT 数据接入传输](ADR-033-push-agent-data-ingest-transport.md)
- [ADR-034：AI Hub 只提供企业身份，业务应用自主授权](ADR-034-identity-provider-application-owned-authorization.md)
