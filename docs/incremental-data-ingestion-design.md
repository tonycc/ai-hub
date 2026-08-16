# 增量数据汇聚与统一治理设计与实施方案

| 项目 | 内容 |
| --- | --- |
| 版本 | V1.0（M7 已实施） |
| 本次变更 | 新增"数据汇聚"能力线：以贴源层（Raw/ODS）+ 定期增量拉取实现各业务应用数据向平台的汇聚，支撑统一治理与 AI 消费；**同时下线并删除 M2 实时事件投影能力线** |
| 适用对象 | 产品、架构、前后端、数据、安全、运维团队 |
| 关联基线 | [总体设计与实施](unified-internal-app-platform-product-and-implementation.md)、[实施计划](implementation-plan.md)、[ADR-032](adr/ADR-032-incremental-ingestion-replaces-m2.md) |
| 状态 | **已实施并合入 main**（M7-01～06）；第 8 节开放问题已冻结为决策（界面化配置见 2.5.1，已实现） |

## 1. 背景与目标

### 1.1 业务需求

公司内部的业务应用需要把数据**定期同步到平台**，由平台综合各应用的数据进行**统一治理**，并**提供给 AI 使用**。

关键约束：

- **定期增量同步**（非实时）：按周期（分钟/小时/天）同步变化的数据，不要求秒级时效。
- **平台统一治理**：平台侧对多来源数据进行检索、对齐、审计、口径管理。
- **供 AI 使用**：AI 消费治理后的数据视图，需要"干净、有来源、有版本、可回溯"的数据。

### 1.2 为什么用数据平台思路替代实时事件投影

沿用数据平台（数仓）的**贴源层（Raw / ODS）**模式，**替代** M2 的实时事件投影：

- 贴源层**原样接收**各应用数据，平台端不为每种业务对象定制建表，**接入新应用/新对象的边际成本趋近于零**（M2 要求每接入一种对象就在平台端建专属投影表、写投影逻辑，耦合重）。
- 定期增量比 M2 的实时事件链路**省传输、基础设施更轻**（不需要 RabbitMQ/Outbox/Inbox）。
- 贴源层天然**可重放、可审计、可回溯**任意时间点状态，契合治理与 AI 审计需求。
- 实际业务**无实时性需求**：定期增量即可满足，M2 为零丢失/秒级时效付出的复杂度在本场景是净负担。

### 1.3 退役 M2 实时事件投影（重要决策）

本方案**下线并删除** M2 实时事件投影能力线，由本方案的增量数据汇聚承接其"应用数据向平台汇聚"的职责。

**退役对象**

- 能力：`EVENT_PUBLISHER`、`EVENT_CONSUMER`、`PROJECTION_SOURCE`、`PROJECTION_READER` 四种事件/投影能力。
- 组件：RabbitMQ 基础设施、应用侧 Outbox/Inbox、平台投影 Worker、`platform_projection` Schema。
- 部署：`standard-events` 档位（退役后仅保留 `base-access` 基础接入档位）。
- 配套：M2 运行时门禁（`scripts/ci/m2-runtime.sh`）、事件契约（AsyncAPI / CloudEvents schema）、参考应用的事件部分、SDK 的 `events.py`。

**退役后对应用的影响**：原需"可靠传播业务事实给平台"的场景，改为通过本方案的**定期增量同步**承载（时效性从秒级降为同步周期级，已确认满足业务需求）。平台不再提供实时事件订阅能力。

**退役范围与影响清单详见第 9 节。**

### 1.4 目标与非目标

**目标**

1. 平台端提供通用的数据贴源层（Raw 层），应用接入**零定制建表**。
2. 提供**定期增量拉取**通道（平台拉取模式），支持全量与增量两种同步模式。
3. 提供**当前态视图**供统一检索与 AI 消费，保留历史供审计回溯。
4. 复用既有身份、权限、审计、应用注册能力，延续数据所有权边界。

**非目标**

- 不做实时同步（实时事件能力随 M2 一并退役）。
- 不做通用 ETL/可视化建模工具（治理加工按需，逐步建设）。
- 第一阶段不做跨应用的语义对齐（语义层仍按既有规划按需启用）。

## 2. 核心设计

### 2.1 总体数据流

```
业务应用                        平台
   │                              │
   │  提供增量导出 API              │  ┌─────────────────────────────┐
   │  GET /export?type=X&since=N  │  │ 汇聚调度器（Pull 定时任务）    │
   │ ◄────────────────────────────│──┤  · 管理每个来源的同步位点       │
   │  返回变化的记录（含版本/操作）  │  │  · 定期按位点拉取增量          │
   │                              │  └──────────────┬──────────────┘
   │                              │                 ▼
   │                              │  ┌─────────────────────────────┐
   │                              │  │ 贴源层 raw_ingest（追加日志）  │
   │                              │  │  · 原样接收，含操作/版本/来源  │
   │                              │  └──────────────┬──────────────┘
   │                              │                 ▼
   │                              │  ┌─────────────────────────────┐
   │                              │  │ 当前态表（批内事务增量维护）    │
   │                              │  │  · 供统一检索 / AI 消费        │
   │                              │  └─────────────────────────────┘
```

### 2.2 数据契约（应用需实现的导出接口）

每个接入应用需提供一个**增量导出接口**，供平台拉取：

```
GET {app_base_url}/ai-hub/export?object_type={type}&since_version={n}&limit={n}
```

**认证与授权**：平台用 M1 服务身份令牌调用，但应用侧必须校验的不只是"令牌合法"，还须校验**专用 scope `ai_hub.ingest.export`**。该接口暴露的是应用的全量业务数据，不能允许任何持有平台服务令牌的调用方随意拉取——scope 在应用注册/接入能力登记时由平台为"数据汇聚"能力单独签发。

**响应契约**（最外层信封固定；`payload` 结构见下方"payload 契约化"硬要求）：

```json
{
  "object_type": "device",
  "payload_contract_version": "device.v3",
  "records": [
    {
      "object_id": "E-102",
      "operation": "upsert",
      "version": 1234,
      "payload": { "name": "机床", "status": "normal" }
    },
    {
      "object_id": "E-207",
      "operation": "delete",
      "version": 1235,
      "payload": null
    }
  ],
  "has_more": false,
  "high_watermark": 1235
}
```

**字段语义**

| 字段 | 说明 |
| --- | --- |
| `object_id` | 应用内的稳定对象 ID |
| `operation` | `upsert`（新增/更新）或 `delete`（删除）。**删除必须显式表达**——这是增量区别于全量的关键 |
| `version` | 应用侧权威版本号，**在 (应用, 对象类型) 范围内全序单调递增**（见下方硬要求 1），用于位点推进与乱序防护 |
| `payload` | 对象当前完整快照，**结构遵循已登记的 payload 契约**；`delete` 时为 `null` |
| `payload_contract_version` | 本批次 payload 遵循的契约版本标识 |
| `has_more` / `high_watermark` | 分页与本批次最高版本位点，平台据此推进位点 |

**对应用的硬要求**

1. **version 全序单调**：`version` 必须在 `(应用, 对象类型)` 范围内**全序单调递增**（不是"每对象独立版本号"——位点按 `(应用, 对象类型)` 管理，若 version 只在单对象内单调，位点推进会失效）。同时 `version` 的分配顺序必须与**事务提交顺序**一致（见 2.2.1 位点稳定性）。
2. **删除必须能被捕获并上报**（应用需有软删除或删除日志，否则平台无法感知对象消失）。
3. 导出接口必须支持"按 `since_version` 增量查询"且结果按 `version` 有序。
4. 同一对象在同一版本只出现一次（应用侧保证导出的一致性快照）。
5. **payload 契约化**：每个 `object_type` 的 `payload` 必须是**显式登记的版本化契约**（字段经过筛选/脱敏），**不得直接 dump 表行**（见 2.2.2）。

### 2.2.1 位点稳定性：并发事务下的版本倒挂（必须满足）

仅要求"`version` 单调递增"**不足以保证不丢数据**。经典事故：事务 A 先取到 `version=1000`（序列/时间戳在事务内分配），但提交晚于取到 `version=1001` 的事务 B；平台拉到 1001 并把位点推进到 1001 后，A 才提交——`version=1000` 的记录永远低于位点，**被永久漏拉**。数据库序列和变更时间戳都是**事务内分配**的，与**提交顺序**天然不一致，因此"用序列或时间戳"单独使用正是踩坑写法。

应用必须**任选其一**满足位点稳定性（在接入认证中验证）：

- **安全回看窗口（推荐，默认）**：平台拉取时用 `since_version = last_version - 安全边距`（版本回看）或按时间回看 N 秒，重复记录靠幂等唯一约束去重。实现最简单，是默认方案。**边距定值指引**：按时间回看时边距应**大于应用侧最长写事务的持续时间**；按版本回看时边距应覆盖该时长内可能产生的版本跨度。边距作为**接入配置项**随应用登记（结合该应用的事务特征设定）。
- **提交时刻分配 version**：应用保证 `version` 在事务**提交时**分配（如单行计数器加锁、提交序列表），使 version 序与提交序一致。
- **排除未决事务**：导出查询过滤掉尚未提交事务的可见范围（PostgreSQL 可用 `pg_current_snapshot()`）。

对应地，平台调度器默认按**安全回看窗口**拉取（见 2.5），一致性认证（M7-04）必须包含"并发写入场景下无漏拉"的检查项。

### 2.2.2 payload 契约化（防止退化为共享数据库集成）

总体设计明令"禁止将应用业务表定时全量复制到 platform_db"。本方案虽把应用数据成建制汇聚进平台库，但与之的根本区别在于：数据走**受治理的导出契约**（稳定、版本化、可脱敏），而非直连扫表。为守住这条边界：

- 每个 `object_type` 的 `payload` 必须是**显式登记的版本化契约**：字段经过筛选与脱敏，结构有版本（`payload_contract_version`），契约（至少其 schema 指纹）登记进平台。
- **禁止直接 dump 表行**：未登记的字段不得出现在 payload 中；契约变更须升版本并向后兼容或走废弃流程。
- 治理层与 AI 依据登记的契约理解字段语义——这也是 AI 治理"知道每个字段是什么"的前提。
- 退化预警：若 payload 变成无登记的任意表行中转，本机制即退化为"经 JSONB 中转的共享数据库集成"，正是原设计极力避免的反模式，认证与评审应拦截。

### 2.3 贴源层（Raw 层）数据模型

位于平台数据库的独立 Schema（如 `platform_raw`），独立迁移、独立写入角色，**任何应用不得直接读写**。

```sql
-- 同步位点：每个 (应用, 对象类型) 一条，平台统一管理
raw_sync_cursor (
  source_application_id  TEXT,
  object_type            TEXT,
  last_version           BIGINT NOT NULL DEFAULT 0,  -- 已同步到的版本位点
  last_synced_at         TIMESTAMPTZ,
  last_status            TEXT,                        -- ok / failed
  PRIMARY KEY (source_application_id, object_type)
)

-- 同步批次：每次拉取一条
raw_ingest_batch (
  batch_id               UUID PRIMARY KEY,
  source_application_id  TEXT NOT NULL,
  object_type            TEXT NOT NULL,
  sync_mode              TEXT NOT NULL,               -- full / incremental
  from_version           BIGINT,
  to_version             BIGINT,
  record_count           INT,
  status                 TEXT NOT NULL,               -- running / loaded / failed
  started_at             TIMESTAMPTZ,
  finished_at            TIMESTAMPTZ,
  error                  TEXT
)

-- 原始变更记录：追加式日志，不覆盖
raw_change_record (
  id                     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  batch_id               UUID NOT NULL REFERENCES raw_ingest_batch(batch_id),
  source_application_id  TEXT NOT NULL,
  object_type            TEXT NOT NULL,
  object_id              TEXT NOT NULL,
  operation              TEXT NOT NULL,               -- upsert / delete
  version                BIGINT NOT NULL,
  payload                JSONB,
  payload_contract_version TEXT,                      -- 本记录遵循的 payload 契约版本（审计/对账重放需知）
  content_hash           TEXT,                        -- payload 指纹，变化检测/对账
  received_at            TIMESTAMPTZ NOT NULL DEFAULT now()
)
-- 索引：
--   (source_application_id, object_type, object_id, version DESC)  -- 历史回溯/对账
--   (source_application_id, object_type, version)                   -- 位点/对账
--   payload GIN 索引                                                -- JSONB 检索
-- 幂等唯一约束：(source_application_id, object_type, object_id, version)
--   —— 应用重发同一版本时直接跳过，保证幂等

-- 当前态：普通表，批次装载的同一事务内增量维护（非物化视图）
raw_current_state (
  source_application_id  TEXT NOT NULL,
  object_type            TEXT NOT NULL,
  object_id              TEXT NOT NULL,
  payload                JSONB,                       -- 当前存活内容；已删除对象整行移除
  version                BIGINT NOT NULL,             -- 当前态对应的版本
  payload_contract_version TEXT,
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source_application_id, object_type, object_id)
)
-- 索引：payload GIN 索引（供检索/AI）
```

**设计要点**

- **追加式日志**：`raw_change_record` 同一对象可有多条不同 `version` 的记录，保留完整变化历史，支撑审计与回溯；它同时是**对账与重建的权威来源**。
- **幂等唯一约束** `(应用, 对象类型, 对象ID, 版本)`：应用重发、平台重拉同一位点时不产生重复。
- **位点由平台统一管理**（`raw_sync_cursor`）：应用无状态，是"平台拉取"模式的核心优势。
- **当前态是普通表而非物化视图**：`raw_current_state` 在批次装载的**同一事务内**增量维护（见 2.4），避免 `REFRESH MATERIALIZED VIEW` 对只增不减的全量历史做全表 `DISTINCT ON` 的成本随数据量线性上升。

### 2.4 当前态维护（供治理与 AI 消费）

当前态表 `raw_current_state` **在批次装载的同一事务内**增量维护，而非事后刷新物化视图：

**增量批次（incremental）**：对批次内每条记录——
- `upsert` 且 `version` 大于当前态已有 `version` → 更新/插入当前态行；`version` 不大于已有值 → 跳过（乱序/重复防护）。
- `delete` 且 `version` 大于当前态已有 `version` → **删除**当前态行。

**全量批次（full）**：除按上述 upsert 应用外，还须**合成删除墓碑**（见 2.4.1）——这是 full 重建能正确反映删除的前提。

**优点**：当前态永远新鲜（无物化视图的刷新窗口与额外时效损耗）、查询走普通表直读；Raw 日志退化为审计/对账/重建来源。对账时用 Raw 日志重放校验当前态一致性。

**热点对象升级**：当某类对象被高频查询/复杂关联时，从当前态**物化为强类型专属表**（按需，非接入前置）。

### 2.4.1 全量（full）重建的删除语义（必须满足）

追加日志 + 折叠存在隐含漏洞：**full 模式导出里不会有 delete 记录**。若对象在两次同步间被删除、且删除日志已截断（或位点丢失后重跑 full），full 导出中只是"没有该对象"，而 Raw 层它的最后一条 upsert 仍在当前态——**已删除对象在平台上"永生"**。

因此 full 批次装载时必须补一条明确语义：

> **full 批次的缺席即权威**：full 模式下，对"当前态中存在、但本次 full 导出中缺席"的 `(应用, 对象类型)` 范围内对象，合成 `delete` 记录（墓碑）。**全量快照中不存在就是不存在，这是快照语义的本质**——因此 full 的合成墓碑**绕过版本比较**，直接删除当前态行（不受 2.4"version 更大才生效"规则约束）。

**为什么不能只靠版本比较**：若墓碑版本取本次 full 的 `high_watermark`，会有反例——对象 X 在 `version=100` 更新（当前态 X@100）后被删除，其余对象版本均低于 100；重跑 full 时导出中无 X，`high_watermark`（如 95）< 100，墓碑 X@95 被"version 更大才生效"规则跳过，X 依然存活。"删除前最后被修改的对象"最容易触发此洞，故平台侧以"缺席即权威"为准。

**墓碑版本与 `high_watermark` 的契约澄清**：

- 平台侧：Raw 日志中墓碑的 `version` 取 `max(该对象当前态版本 + 1, high_watermark)`，维持日志在 `(应用, 对象类型)` 内的版本有序性；当前态删除直接执行。
- 应用侧契约：full 模式下应用返回的 `high_watermark` 应为**应用侧版本序列的当前高水位**（包含未导出的删除所消耗的版本），而非"本批导出记录的最大版本"——这样即使依赖版本比较，墓碑版本也天然 ≥ 被删对象的最后版本。两条规则互补：平台侧"缺席即权威"为主（不依赖应用实现正确），契约澄清为辅。

incremental 批次不合成墓碑（删除由应用显式上报）。

### 2.5 汇聚调度器（Pull 模式）

平台侧新增一个**汇聚调度器**组件（独立进程，与 HTTP API / M2 Worker 分离）：

**职责**

1. 读取接入配置（哪些应用、哪些对象类型、同步周期、拉取地址）。
2. 按周期对每个 `(应用, 对象类型)` 发起拉取：从 `raw_sync_cursor` 取位点 → 调应用导出接口 → 写入批次与变更记录 → **同一事务内**维护当前态 → 推进位点。
3. 失败处理：记录批次失败、不推进位点（下次从原位点重拉），按既有告警与责任路由上报。

**关键语义**

- **安全回看窗口（默认）**：拉取时 `since_version = last_version - 安全边距`（版本回看）或按时间回看 N 秒，重复记录靠幂等约束去重——这是 2.2.1 位点稳定性的默认实现，防止并发事务版本倒挂导致漏拉。
- **位点单调推进，失败回退**：只有整批 `loaded` 成功才推进 `last_version`；中途失败保持原位点，下次重拉（配合幂等约束天然安全）。位点记录的是"已确认的高水位"，回看边距只影响拉取起点、不影响位点推进逻辑。
- **全量与增量兼容**：`sync_mode=full` 时拉全量（可用于初始回填或重建），`incremental` 时按位点拉增量。首次接入可先跑一次 `full` 建基线，再转 `incremental`。
- **full 批次合成墓碑**：full 装载时对"当前态存在但本次导出缺席"的对象合成 delete（见 2.4.1）。
- **full 与 incremental 串行化**：同一 `(应用, 对象类型)` 的 full 批次进行中（可能跨多页、多次 HTTP 请求）**不得并行执行 incremental**，否则墓碑差集会被并发写入污染。full 的"缺席对象差集"必须在该 full 的**全部分页完成后**统一计算（基于完整快照与当时当前态求差），再一次性合成墓碑并随批次提交。
- **并发预算**：调度器对每个应用限流，避免同步压垮业务应用；**不同** `(应用, 对象类型)` 可并行，同一 `(应用, 对象类型)` 内串行。

### 2.5.1 配置来源：界面化（Portal 权威，M7 后续）

首期起，所有汇聚配置与运维动作**一律经门户界面完成**，调度器/对账/重建/裁剪以**平台库中的权威配置为准**（不再以仓库 JSON 为运行时来源）：

- **存储**：`platform_core.ingest_source`（每个 `(应用, 对象类型)` 一行：导出地址、启停、`interval_seconds`、`lookback_versions`、`page_limit`）与 `platform_core.ingest_policy`（单行全局策略：保留版本数/按天、payload 上限、每页默认与硬上限、定时对账开关与周期）。仓库 `deploy/operations/ingest-sources.json` 仅作 bootstrap 种子或 CI 替身，运行时以库为准。
- **界面**：独立「数据接入」页（`/#/platform/ingest`），含「数据来源 / 同步与保留设置 / 同步与维护」三个区；重新同步与清理历史带二次确认。
- **权限**：读 `platform.operations.read`（或新增 `platform.ingest.read`）；写与动作 `platform.ingest.write`，默认仅授予平台运维/管理员角色；所有变更与动作写 `audit_event`。
- **API**：`GET/PUT` 源与策略；`POST` 触发 sync / reconcile / rebuild / prune。CLI（CI、`m7-runtime`）与界面复用同一 service，界面不是第二套逻辑。
- **可裁回**：若后续某项要从界面下线改回 CLI/文件，只藏前端入口，API 标记 deprecated 或仅保留 CLI，配置仍以库为权威。
- **明确不做**：对象存储/快照压实界面、无确认的自动 full 重建、把生产目标（RPO 等只读文件）改为可写。

### 2.6 同步时序与一致性

```
首次接入：
  1. 平台登记应用 + 对象类型，初始化位点 last_version = 0
  2. 调度器跑 full 模式建基线（对缺席对象合成 delete 墓碑）
     → 位点推进到基线 high_watermark
  3. 转入 incremental，按周期拉 version > (last_version - 安全边距) 的变更

日常增量：
  每个周期：取位点（减回看边距）→ 拉取 → 追加写 raw_change_record
            → 同事务增量维护当前态 → 推进位点

兜底：
  · 位点丢失/数据漂移 → 重跑 full 重建基线（含墓碑合成，传播删除）
  · 定期对账 → 用 raw_change_record 重放比对当前态（content_hash/记录数），发现漂移告警并重建
```

**乱序与重复**：以 `version` 为权威 + 位点单调推进 + 安全回看窗口 + 幂等唯一约束，重复拉取/重发不会产生错误结果；当前态维护时仅在 `version` 更大时才更新，旧版本不会覆盖新版本；并发事务的版本倒挂由回看窗口兜底（见 2.2.1）。

## 3. 与既有平台能力的关系

| 既有能力 | 复用方式 |
| --- | --- |
| 应用注册（`app_registry`/`app_management`） | 接入应用先登记，声明其启用"数据汇聚"；同步所需的服务凭据沿用既有签发/轮换/吊销机制 |
| 身份与服务身份（M1） | 平台拉取应用导出接口时，用平台服务身份令牌认证 |
| 审计（`audit`） | 每次批次、位点变更、失败、重建均落审计 |
| 运维（`operations`） | 同步健康（位点滞后、批次失败率）纳入运维摘要与 OpenMetrics；移除原投影/RabbitMQ 健康项 |
| M2 事件投影 | **下线删除**：实时事件与投影能力整体退役，由本方案承接数据汇聚职责（见第 9 节） |

## 4. 安全与边界

- **数据所有权**：应用只通过导出接口暴露"已治理、版本化"的数据；平台不直连应用数据库扫表（延续既有红线）。
- **导出接口授权**：应用侧校验平台服务身份令牌 **且** 专用 scope `ai_hub.ingest.export`（见 2.2）。
- **访问隔离**：Raw 层按 `source_application_id` 逻辑隔离；治理层/AI 可跨应用读，任何应用**不能读其他应用**的 Raw 数据。
- **凭据**：同步用独立服务凭据，生产与非生产环境隔离（延续既有约定）。
- **位点与凭据保护**：位点、批次、原始记录均在平台侧，应用不可见平台内部存储。

### 4.1 消费面权限模型（治理层 / AI 的访问控制）

验收标准第 5 条"权限边界正确"需要可验的授权规则，因此定义：

- **权限码**：`platform.data.read`（读取汇聚数据当前态/历史）。M7-03 的只读查询 API 以此权限码鉴权。
- **授权对象**：治理人员、AI 服务的服务身份。AI 以服务身份调用查询接口，按 `platform.data.read` + （可选）按 `source_application_id`/`object_type` 的范围授权。
- **最小粒度**：默认授予跨应用读（治理/AI 场景需要）；如需限制，可按对象类型/来源应用做行级范围收缩。
- **审计**：所有对汇聚数据的查询按 `audit` 落审计（谁、何时、查了哪个应用/对象类型）。

## 5. 与 SDK / 接入指南的联动

- **SDK**：为接入应用提供"增量导出接口"的参考实现/辅助（版本序列生成、导出分页、信封构造、服务身份校验），降低应用侧实现成本；同时**移除事件相关模块（`events.py`）**。
- **接入指南**：新增"数据汇聚"一节，说明导出接口契约、`operation`/`version` 语义、删除捕获要求、首次全量建基线流程；**删除"按需启用事件"一节**。
- **一致性认证**：为"数据汇聚"能力新增认证检查：**导出接口可达且校验专用 scope `ai_hub.ingest.export`**、**version 在 (应用,对象类型) 全序单调**、**并发写入场景下无漏拉**（位点稳定性，2.2.1）、**删除可捕获并显式上报**、**幂等正确**、**payload 符合已登记契约**（2.2.2）；**移除事件发布/消费/投影的认证配置**。

## 6. 实施方案（分阶段）

> 本方案建议作为新的工作线编号（暂定 M7：数据汇聚）。M7-01 ~ M7-05 建设增量汇聚能力并验证达标后，M7-06 执行 M2 退役。每阶段给出任务、产物与验证。

### M7-01 贴源层与位点（地基）

- 任务：建 `platform_raw` Schema、`raw_sync_cursor` / `raw_ingest_batch` / `raw_change_record` / `raw_current_state` 四表、独立迁移与角色；实现批次写入、幂等约束与**批内事务维护当前态**（含 full 墓碑合成）。
- 产物：Alembic 迁移、数据访问层、单元测试。
- 验证：写入/幂等/约束测试通过；当前态增量维护（含乱序跳过、delete 移除、full 墓碑合成）正确；应用角色无法跨边界读写的权限门禁通过。

### M7-02 汇聚调度器（Pull 核心）

- 任务：实现调度器进程（位点读取、**安全回看窗口**增量拉取、批次落库、同事务维护当前态、位点推进、失败回退）；接入配置模型；限流与并发预算。
- 产物：调度器服务、配置模型、运维指标。
- 验证：用参考应用模拟导出接口，验证位点单调推进、**并发事务版本倒挂下无漏拉**、失败重拉不重复、乱序折叠正确。

### M7-03 当前态消费面与权限

- 任务：基于当前态普通表的统一检索查询接口（供 AI/治理），以权限码 `platform.data.read` 鉴权；热点对象物化为强类型表的机制（按需）。
- 产物：只读查询 API、权限码与授权规则、AI 消费示例。
- 验证：查询接口按 `platform.data.read` 正确鉴权与审计；检索性能达标；AI 可查询且能回溯历史；应用不可读他应用数据。

### M7-04 SDK 导出辅助 + 接入指南 + 认证

- 任务：SDK 提供导出接口辅助并移除 `events.py`；接入指南新增"数据汇聚"章节、删除"按需启用事件"一节；一致性认证新增数据汇聚检查、移除事件类认证。
- 产物：SDK 版本、文档、认证用例。
- 验证：参考应用通过数据汇聚接入认证；文档与 SDK 一致。

### M7-05 对账与重建

- 任务：实现 `full` 模式重建、定期 content_hash/记录数对账、漂移告警。
- 产物：`modules/ingest/reconcile.py` / `rebuild.py`；CLI `ai-hub-ingest-reconcile`（漂移退出码 1）与 `ai-hub-ingest-rebuild log|source`；运维手册 `docs/runbooks/ingest-reconcile.md`（定时对账 + 漂移告警约定）。
- 验证：人为制造漂移能对账发现；从空 Raw 层经 full 重建后与源一致。

### M7-06 退役 M2 实时事件投影

- 前置条件：M7-01 ~ M7-05 验收通过，增量汇聚能力已在至少一个参考场景验证达标；确认无在运行的应用依赖 M2 事件/投影能力。
- 任务：按下线清单（第 9 节）删除 M2 代码、迁移、部署组件、CI 门禁、契约、SDK 事件模块与参考应用事件部分；从 `compose.yaml` 移除 RabbitMQ 与 `standard-events` 档位；清理 `platform_projection` Schema。**删除前打 git tag 归档（如 `archive/m2-event-projection`），并将事件契约（AsyncAPI / CloudEvents schema）归档保存**，供未来若出现实时需求时考古。
- 产物：删除变更集；git tag `archive/m2-event-projection`；契约归档于 `docs/archive/m2-event-projection/`；唯一部署档位 `base-access`；静态门禁、单元测试与 `scripts/ci/m7-runtime.sh` 通过。
- 验证：`base-access` 档位从全新数据卷完整启动并通过 M1 及 M7 运行时门禁；代码/配置路径无 RabbitMQ、Outbox、Inbox、projection、`standard-events` 能力残留（负向门禁测试除外）；备份/恢复流程不再包含 RabbitMQ 与投影库。
- 状态：**已完成**（合入 `main`，PR #7）。

### 回滚与降级

- 贴源层与调度器为**新增独立组件**，可整体停用而不影响既有 M0–M4.1 能力。
- 当前态表可从 Raw 日志重放重建；强类型表删除后可从当前态恢复。
- 不使用跨数据库两阶段提交；所有写入限定在平台库内单事务。
- **M2 退役为单向操作**：M7-06 执行后事件链路不可恢复，须以"先建成增量汇聚并验证达标"为退役前置，避免能力真空期；事件契约经 git tag 归档，可在确有需要时考古人恢复。

## 7. 验收标准

1. 一个新应用接入数据汇聚，**平台端无需为其定制建表**，仅登记配置与 payload 契约即可开始同步。
2. 定期增量同步正确：**并发事务下无漏拉**（位点稳定性，回看窗口/提交序/快照过滤之一 + 认证验证）、不重（幂等约束）、删除可见（`operation=delete` 生效）。
3. **full 重建正确传播删除**：对当前态存在但 full 导出缺席的对象合成墓碑，重建后当前态与源一致（含已删除对象不残留）。
4. 当前态表与源数据一致（经对账重放验证），历史可回溯；当前态批内事务维护、无刷新窗口。
5. 平台故障后恢复，同步从位点续传无重复。
6. AI/治理通过当前态查询接口消费数据，按权限码 `platform.data.read` 鉴权且权限边界正确（应用不可读他应用数据）、全程审计。
7. **M2 实时事件投影能力线已完整下线删除**：无 RabbitMQ/Outbox/Inbox/投影/`standard-events` 残留，`base-access` 成为唯一部署档位，全部静态与运行时门禁通过；事件契约已归档。

## 8. 开放问题（已冻结决策）

1. **同步周期默认值**：按源（`(应用, 对象类型)`）在门户配置 `interval_seconds`，示例默认 60s；文档另给时效分层建议（如设备 15min / 一般业务 1h / 慢变 24h），具体值由各源按业务时效在界面设置。见 2.5.1。
2. **历史保留策略**：每对象保留**最近 100 个版本**，可选叠加「保留最近 90 天」作为第二道闸；提供**裁剪**运维动作（默认 dry-run 预览、确认后应用），经界面/CLI 触发。**快照压实与对象存储暂不引入**，后续按需评估。全局策略见 2.5.1。
3. **大对象/大批量**：`page_limit` 默认 200、硬上限 5000（可配置）；单条 `payload` 超过 1 MiB 拒绝写入（上限可配置）。超大 payload 的对象存储方案**暂不引入**，按需评估。
4. **全量重建的触发方式**：**手动运维触发**（界面/CLI，二次确认）；允许**定时对账**（开关 + 周期可配），但**漂移后自动 rebuild 首期不开放**——对账仅呈现漂移清单，由人工决定是否重建。
5. **M5（多消费方治理）调整**：M2 退役后，实施计划 M5 中与事件相关的验证项需相应改为围绕数据汇聚能力（多消费方对导出契约、scope、位点隔离的复用验证），建议在实施计划中同步标注。
6. **应用主动推送（后续增强，首期不做）**：为降低同步延迟，可采用**混合模式**——应用只向平台推送一条「有变更」的轻量提示（webhook/事件），平台收到后**立即对相应源触发一次拉取**；权威数据仍以拉取与对账为准，推送仅作加速信号，不承载业务数据。如此兼顾时效与幂等/背压/乱序安全。首期不实现，需要时单独立项。

## 9. M2 实时事件投影下线清单

以下为 M7-06 退役 M2 时需删除/修改的完整影响面（基于代码基线梳理）。

### 9.1 后端代码

- 删除模块 `backend/src/ai_hub_platform/modules/projection/`（`service.py`、`worker.py`）。
- 删除/调整引用：`modules/operations/service.py` 中投影与 RabbitMQ 健康检查、`api/operations.py` 中事件/投影相关摘要项、`operations/backup.py` 与 `operations/release.py` 中对投影库/RabbitMQ 的处理。
- 配置项清理：`config.py` 中 `RABBITMQ_URL`、`INTEGRATION_CAPABILITIES` 等事件相关配置；`.env.example`、`backend/.env.example` 同步清理。
- 能力枚举：移除 `EVENT_PUBLISHER`、`EVENT_CONSUMER`、`PROJECTION_SOURCE`、`PROJECTION_READER`（应用注册、接入能力登记、认证配置中的相关项）。

### 9.2 数据库与迁移

- 删除 `backend/migrations/versions/projection/`（投影 Schema 迁移）；清理 `migrations/env.py`、`test_migration_contracts.py` 中的 projection 相关逻辑。
- 删除 `platform_projection` Schema 及其中立参考投影（`example_record_summary`）、`projection_inbox` 等表。
- 更新 `deploy/postgres/verify/role-boundaries.sql`：移除投影写入角色与 RabbitMQ 相关的权限校验。

### 9.3 部署与基础设施

- `deploy/compose.yaml`：移除 RabbitMQ 服务、投影 Worker 服务及 `standard-events` profile；保留唯一档位 `base-access`。
- 删除 `deploy/rabbitmq/`（含 `bootstrap.sh`）。
- `deploy/component-lock.json` 与 `docs/component-upgrade-policy.md`：移除 RabbitMQ 组件锁定项。
- `deploy/operations/`：`production-targets.json`、`release-manifest.schema.json`、`alert-rules.json` 中移除事件积压、DLQ、投影滞后、RabbitMQ 健康等指标与告警项。
- `.env.example`、部署文档中移除 RabbitMQ 凭据与事件档位说明。

### 9.4 CI / 门禁

- 删除 `scripts/ci/m2-runtime.sh`（M2 运行时门禁）。
- `scripts/ci/all.sh`、`scripts/ci/deploy.sh`、`scripts/local/start.sh`：移除 `standard-events` 档位与 M2 相关步骤；`start.sh` 默认档位改为 `base-access`。
- 更新 `.github/workflows/ci.yml`：移除 M2 运行时作业；`Required gate` 汇总调整。
- 移除 M4 各运行时脚本（`m4-resilience-runtime.sh`、`m4-recovery-runtime.sh`、`m4-observability-runtime.sh`、`m4-release-runtime.sh` 等）中对 RabbitMQ/事件积压/投影的演练步骤，备份/恢复脚本不再含 RabbitMQ 与投影库。

### 9.5 SDK 与参考应用

- SDK：删除 `sdk/python/src/ai_hub_sdk/events.py` 及对应测试 `test_event_contracts.py`；`client.py`/`__init__.py` 移除事件导出。
- 参考应用 `examples/standalone-app/`：移除事件部分（`events.py`、`consumer.py`、`alembic-event-*.ini`、事件迁移、Outbox/Inbox 表），保留 API-only 接入。
- 删除 `examples/sdk/api_only.py` 之外的事件示例（若有）。

### 9.6 契约与前端

- 删除 `contracts/events/`（`ai-hub.asyncapi.yaml`、`cloud-event.schema.json`、`example-record-*.schema.json`）。
- 开发者中心资产目录（`modules/developer/service.py` 的 `ASSETS`）：移除 `platform-asyncapi`、`cloud-event-schema` 两项。
- 前端：`src/data/platformCapabilities.js`、`OperationsCenterView.vue`、`PlatformSettingsView.vue` 中移除事件/投影能力项与健康展示；开发者中心"可选能力"移除四个事件/投影能力标签。

### 9.7 文档

- 更新总体设计文档与实施计划：移除 M2 章节与事件/投影契约描述，登记"数据汇聚"能力，标注 M2 已退役及退役版本。
- 删除/归档 M2 相关验收报告引用；`deploy/README.md`、各 runbook（`backup-restore.md`、`alert-response.md`）移除 RabbitMQ/投影/事件相关操作。
- `docs/local-full-flow-test-guide.md`、开发者接入指南：移除事件接入内容。

### 9.8 退役验证门禁

- 全仓检索 `rabbitmq|RabbitMQ|RABBITMQ|Outbox|Inbox|projection|standard-events|EVENT_PUBLISHER|EVENT_CONSUMER|PROJECTION_SOURCE|PROJECTION_READER` 无残留，**检索范围限定代码与配置路径**（`backend/`、`sdk/`、`deploy/`、`contracts/`、`scripts/`、`src/`、`examples/`），避免对文档叙述性文字（如 `Inbox`）误伤；历史验收报告等归档文档除外。
- `base-access` 从全新数据卷启动，M1 与 M7 全部门禁通过。
- 备份/恢复演练确认不再依赖 RabbitMQ 与投影库。

---

**下一步**：M7 能力线已交付。后续优先：生产环境实例化（密钥/HTTPS/异机备份）；拍板第 8 节开放问题（同步周期、历史保留、大批量、重建触发）；按改口后的 M5 开展多消费方治理验证。事件契约考古见 `docs/archive/m2-event-projection/` 与 tag `archive/m2-event-projection`。