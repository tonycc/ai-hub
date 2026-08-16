# 数据汇聚对账与重建

当怀疑 `raw_current_state` 与权威变更日志不一致，或位点丢失需要从源重建时使用本流程。

## 对账

用变更日志重放期望当前态，并与 `raw_current_state` 比对记录数与 `content_hash`：

```bash
ai-hub-ingest-reconcile standalone-example example_record
```

- 退出码 `0`：无漂移。
- 退出码 `1`：存在漂移；stdout JSON 含 `drifts[]`（`missing` / `unexpected` / `hash_mismatch` / `version_mismatch`）。
- 建议由运维定时任务执行；漂移时按责任路由告警并执行重建。

## 重建

### 从变更日志重建当前态（不调应用）

```bash
ai-hub-ingest-rebuild log standalone-example example_record
```

删除该 `(应用, 对象类型)` 的当前态行后按 `raw_change_record` 重放折叠。日志与位点不变。

### 从源 full 拉取重建

```bash
ai-hub-ingest-rebuild source standalone-example example_record
```

要求 `deploy/operations/ingest-sources.json` 已配置对应源。调度器以 `force_full=True` 拉取全量（含墓碑合成）并推进位点。

## 建议顺序

1. `ai-hub-ingest-reconcile …` 确认是否漂移。  
2. 若仅当前态损坏：`rebuild log`。  
3. 若日志也不可信或位点丢失：`rebuild source`，再对账一次。
