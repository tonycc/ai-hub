<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import ApiState from '../components/ApiState.vue'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { usePortalSession } from '../stores/session'
import {
  ingestActivateContract,
  ingestApproveCertification,
  ingestCreateCertification,
  ingestGetConfig,
  ingestInferContract,
  ingestListCertifications,
  ingestListContracts,
  ingestListOperations,
  ingestRejectContract,
  ingestRunPrune,
  ingestRunRebuild,
  ingestRunReconcile,
  ingestRunSync,
  ingestSaveContract,
  ingestSavePolicy,
  ingestSaveSource,
} from '../services/ingestApi'

const session = usePortalSession()
const canWrite = computed(() => session.hasPermission('platform.ingest.write'))
const canCertifyOwner = computed(() => session.hasPermission('platform.ingest.certify.data_owner'))
const canCertifyOperator = computed(() => session.hasPermission('platform.ingest.certify.operator'))
const canCertify = computed(() => canCertifyOwner.value || canCertifyOperator.value)
const activeTab = ref('sources')
const loading = ref(false)
const error = ref(null)
const sources = ref([])
const contracts = ref([])
const certifications = ref([])
const runs = ref([])

const tabs = [
  ['sources', '数据来源'],
  ['contracts', '契约'],
  ['policy', '同步与保留设置'],
  ['actions', '同步与维护'],
]

const contractForm = reactive({
  source_application_id: '',
  object_type: '',
  contract_version: '',
  json_schema_text: '{\n  "type": "object",\n  "properties": {}\n}',
  field_classifications_text: '{}',
  compatibility_mode: 'BACKWARD',
})

const certEvidence = reactive({
  rows_validated: 1,
  observation_batch_from: '',
  observation_batch_to: '',
  violation_summary_text: '{\n  "unexempted": []\n}',
  exemption_summary_text: '{\n  "items": []\n}',
  full_regression_status: 'passed',
  incremental_regression_status: 'passed',
  rollback_drill_status: 'passed',
  full_regression_evidence_ref: '',
  incremental_regression_evidence_ref: '',
  rollback_drill_evidence_ref: '',
})

const policyForm = reactive({
  retention_keep_versions: 100,
  retention_keep_days: null,
  payload_max_bytes: 1048576,
  page_limit_default: 200,
  page_limit_max: 5000,
  scheduled_reconcile_enabled: false,
  reconcile_interval_hours: 24,
  push_staging_retention_hours: 24,
})

const sourceDialogVisible = ref(false)
const sourceDialogMode = ref('create')
const sourceOriginallyEnabled = ref(false)
const sourceOriginalTransport = ref('PULL_EXPORT')
const sourceForm = reactive({
  source_application_id: '',
  object_type: '',
  transport_mode: 'PULL_EXPORT',
  export_base_url: '',
  interval_seconds: 60,
  lookback_versions: 100,
  page_limit: 200,
  enabled: false,
  push_protocol_version: '',
  contract_validation_mode: 'AUDIT_ONLY',
  allow_empty_full: false,
})

const actionState = reactive({
  syncMode: 'incremental',
  rebuildSource: '',
  rebuilding: false,
  pruning: false,
})

const driftResult = ref(null)

const pullSources = computed(() =>
  sources.value.filter((item) => (item.transport_mode || 'PULL_EXPORT') === 'PULL_EXPORT'),
)
const enabledCount = computed(() => sources.value.filter((item) => item.enabled).length)
const lastSyncAt = computed(() => {
  const times = sources.value
    .filter((item) => item.last_success_at || lastStatusOk(item))
    .map((item) => item.last_success_at || item.last_sync_at)
    .filter(Boolean)
    .sort()
  return times.length ? times[times.length - 1] : null
})
const lastSyncFailedAt = computed(() => {
  const times = sources.value
    .filter((item) => lastStatusFailed(item) && item.last_sync_at)
    .map((item) => item.last_sync_at)
    .sort()
  return times.length ? times[times.length - 1] : null
})
const lastSyncHint = computed(() => {
  if (lastSyncAt.value) return '任一来源最近成功'
  if (lastSyncFailedAt.value) return `最近失败 ${formatTime(lastSyncFailedAt.value)}`
  return '尚未成功同步'
})

function formatTime(value) {
  return value
    ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'medium' }).format(new Date(value))
    : '—'
}

function formatBytes(value) {
  if (value === null || value === undefined) return '—'
  if (value >= 1048576) return `${(value / 1048576).toFixed(1)} MiB`
  if (value >= 1024) return `${Math.round(value / 1024)} KiB`
  return `${value} B`
}

function isPushSource(row) {
  return (row.transport_mode || 'PULL_EXPORT') === 'PUSH_AGENT'
}

function lastStatusOk(row) {
  return row.last_status === 'ok' || row.last_status === 'COMPLETED' || row.last_status === 'loaded'
}

function lastStatusFailed(row) {
  return (
    row.last_status === 'failed' ||
    row.last_status === 'FAILED' ||
    row.last_status === 'EXPIRED' ||
    row.last_status === 'ABORTED'
  )
}

async function loadAll() {
  loading.value = true
  error.value = null
  try {
    const [config, contractRows, certificationRows, operationRows] = await Promise.all([
      ingestGetConfig(),
      ingestListContracts(),
      ingestListCertifications(),
      ingestListOperations(),
    ])
    sources.value = config.sources
    Object.assign(policyForm, config.policy)
    contracts.value = contractRows
    certifications.value = certificationRows
    runs.value = operationRows
    if (!actionState.rebuildSource && config.sources.length) {
      actionState.rebuildSource = sourceKey(config.sources[0])
    }
  } catch (caught) {
    error.value = caught
  } finally {
    loading.value = false
  }
}

async function loadOperationHistory() {
  try {
    runs.value = await ingestListOperations()
  } catch {
    // The action result remains visible through its toast; a full refresh will
    // expose a history loading error through ApiState.
  }
}

function errorText(caught) {
  return caught?.message || '操作失败'
}

function sourceKey(row) {
  return `${row.source_application_id}:${row.object_type}`
}

function openSourceDialog(mode, row) {
  sourceDialogMode.value = mode
  if (mode === 'edit' && row) {
    Object.assign(sourceForm, row)
    sourceOriginallyEnabled.value = Boolean(row.enabled)
    sourceOriginalTransport.value = row.transport_mode || 'PULL_EXPORT'
  } else {
    Object.assign(sourceForm, {
      source_application_id: '',
      object_type: '',
      transport_mode: 'PULL_EXPORT',
      export_base_url: '',
      interval_seconds: 60,
      lookback_versions: 100,
      page_limit: policyForm.page_limit_default,
      enabled: false,
      push_protocol_version: '',
      contract_validation_mode: 'AUDIT_ONLY',
      allow_empty_full: false,
    })
    sourceOriginallyEnabled.value = false
    sourceOriginalTransport.value = 'PULL_EXPORT'
  }
  sourceDialogVisible.value = true
}

const transportModeLocked = computed(
  () => sourceDialogMode.value === 'edit' && sourceOriginallyEnabled.value,
)
const transportChanging = computed(
  () =>
    sourceDialogMode.value === 'edit' &&
    sourceForm.transport_mode !== sourceOriginalTransport.value,
)

async function saveSource() {
  const isPush = sourceForm.transport_mode === 'PUSH_AGENT'
  if (!sourceForm.source_application_id || !sourceForm.object_type) {
    ElMessage.warning('应用 ID 与数据类型为必填')
    return
  }
  if (!isPush && !sourceForm.export_base_url) {
    ElMessage.warning('拉取模式需要填写数据地址')
    return
  }
  if (isPush && !sourceForm.push_protocol_version) {
    ElMessage.warning('推送模式需要填写协议版本')
    return
  }
  const payload = {
    ...sourceForm,
    export_base_url: isPush ? null : sourceForm.export_base_url,
    push_protocol_version: isPush ? sourceForm.push_protocol_version : null,
    contract_validation_mode: isPush ? 'ENFORCE' : sourceForm.contract_validation_mode,
  }
  try {
    await ingestSaveSource(payload)
  } catch (caught) {
    ElMessage.error(errorText(caught))
    return
  }
  sourceDialogVisible.value = false
  ElMessage.success(sourceDialogMode.value === 'edit' ? '数据来源已保存' : '数据来源已创建')
  await loadAll()
}

function sourcePayload(row) {
  return {
    source_application_id: row.source_application_id,
    object_type: row.object_type,
    transport_mode: row.transport_mode || 'PULL_EXPORT',
    export_base_url: row.export_base_url || null,
    interval_seconds: row.interval_seconds,
    lookback_versions: row.lookback_versions,
    page_limit: row.page_limit,
    enabled: row.enabled,
    push_protocol_version: row.push_protocol_version || null,
    contract_validation_mode: row.contract_validation_mode || 'AUDIT_ONLY',
    allow_empty_full: Boolean(row.allow_empty_full),
  }
}

async function toggleSource(row) {
  try {
    await ingestSaveSource(sourcePayload(row))
    ElMessage.success(row.enabled ? '已启用' : '已停用')
  } catch (caught) {
    row.enabled = !row.enabled
    ElMessage.error(errorText(caught))
  }
}

async function savePolicy() {
  try {
    await ingestSavePolicy({ ...policyForm })
    ElMessage.success('设置已保存')
  } catch (caught) {
    ElMessage.error(errorText(caught))
  }
}

function contractKey(row) {
  return {
    source_application_id: row.source_application_id,
    object_type: row.object_type,
    contract_version: row.contract_version,
    expected_schema_fingerprint: row.schema_fingerprint,
  }
}

function fillContractForm(row) {
  Object.assign(contractForm, {
    source_application_id: row.source_application_id,
    object_type: row.object_type,
    contract_version: row.contract_version,
    json_schema_text: JSON.stringify(row.json_schema, null, 2),
    field_classifications_text: JSON.stringify(row.field_classifications || {}, null, 2),
    compatibility_mode: row.compatibility_mode || 'BACKWARD',
  })
}

async function saveContractDraft() {
  let json_schema
  let field_classifications
  try {
    json_schema = JSON.parse(contractForm.json_schema_text)
    field_classifications = JSON.parse(contractForm.field_classifications_text || '{}')
  } catch {
    ElMessage.warning('契约 Schema 与字段分类必须是合法 JSON')
    return
  }
  try {
    await ingestSaveContract({
      source_application_id: contractForm.source_application_id,
      object_type: contractForm.object_type,
      contract_version: contractForm.contract_version,
      json_schema,
      field_classifications,
      compatibility_mode: contractForm.compatibility_mode,
    })
  } catch (caught) {
    ElMessage.error(errorText(caught))
    return
  }
  ElMessage.success('契约草稿已保存')
  await loadAll()
}

async function inferContractDraft() {
  if (
    !contractForm.source_application_id
    || !contractForm.object_type
    || !contractForm.contract_version
  ) {
    ElMessage.warning('请先填写应用、数据类型和契约版本')
    return
  }
  try {
    const saved = await ingestInferContract({
      source_application_id: contractForm.source_application_id,
      object_type: contractForm.object_type,
      contract_version: contractForm.contract_version,
    })
    fillContractForm(saved)
    ElMessage.success('已从 Raw 推导草稿')
    await loadAll()
  } catch (caught) {
    ElMessage.error(errorText(caught))
  }
}

async function activateContract(row) {
  const replaced = contracts.value.find(
    (item) => item.source_application_id === row.source_application_id
      && item.object_type === row.object_type
      && item.status === 'ACTIVE',
  )
  try {
    await ElMessageBox.confirm(
      [
        `来源 ${row.source_application_id}`,
        `对象 ${row.object_type}`,
        `版本 ${row.contract_version}`,
        `指纹 ${row.schema_fingerprint || '—'}`,
        replaced
          ? `将替换当前 ACTIVE 版本 ${replaced.contract_version}`
          : '当前没有 ACTIVE 版本',
        '激活后无法用现有接口直接撤回。确认激活？',
      ].join('\n'),
      '确认激活契约',
      { type: 'warning', confirmButtonText: '确认激活', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await ingestActivateContract(contractKey(row))
  } catch (caught) {
    ElMessage.error(errorText(caught))
    return
  }
  ElMessage.success('契约已激活')
  await loadAll()
}

async function rejectContract(row) {
  try {
    await ElMessageBox.confirm(
      `拒绝后契约 ${row.contract_version} 不可再激活。确认拒绝？`,
      '确认拒绝契约',
      { type: 'warning', confirmButtonText: '确认拒绝', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await ingestRejectContract(contractKey(row))
  } catch (caught) {
    ElMessage.error(errorText(caught))
    return
  }
  ElMessage.success('契约已拒绝')
  await loadAll()
}

async function createCertification(row) {
  let violation_summary
  let exemption_summary
  try {
    violation_summary = JSON.parse(certEvidence.violation_summary_text)
    exemption_summary = JSON.parse(certEvidence.exemption_summary_text)
  } catch {
    ElMessage.warning('违规摘要与豁免摘要必须是 JSON 对象')
    return
  }
  if (!certEvidence.observation_batch_from || !certEvidence.observation_batch_to) {
    ElMessage.warning('请填写观察批次起止 UUID')
    return
  }
  try {
    await ingestCreateCertification({
      source_application_id: row.source_application_id,
      object_type: row.object_type,
      contract_version: row.contract_version,
      rows_validated: certEvidence.rows_validated,
      observation_batch_from: certEvidence.observation_batch_from,
      observation_batch_to: certEvidence.observation_batch_to,
      violation_summary,
      exemption_summary,
      full_regression_status: certEvidence.full_regression_status,
      incremental_regression_status: certEvidence.incremental_regression_status,
      rollback_drill_status: certEvidence.rollback_drill_status,
      full_regression_evidence_ref: certEvidence.full_regression_evidence_ref,
      incremental_regression_evidence_ref: certEvidence.incremental_regression_evidence_ref,
      rollback_drill_evidence_ref: certEvidence.rollback_drill_evidence_ref,
    })
  } catch (caught) {
    ElMessage.error(errorText(caught))
    return
  }
  ElMessage.success('认证草稿已创建')
  await loadAll()
}

function issueItems(summary, key) {
  if (!summary || typeof summary !== 'object') return []
  const items = summary[key]
  return Array.isArray(items) ? items : []
}

function summarizeCertificationIssues(items) {
  if (!items.length) return '无'
  const codes = [...new Set(items.map((item) => item.code).filter(Boolean))]
  const scopes = [
    ...new Set(
      items.flatMap((item) => [item.object_id, item.path].filter(Boolean)),
    ),
  ]
  const codeLabel = codes.length ? codes.join('、') : '无代码'
  const scopeLabel = scopes.length ? scopes.slice(0, 6).join('、') : '未标明对象/路径'
  return `${items.length} 条 · ${codeLabel} · ${scopeLabel}`
}

function exemptionNote(row) {
  const summary = row.exemption_summary
  if (!summary || typeof summary !== 'object') return ''
  const items = Array.isArray(summary.items) ? summary.items : []
  const notes = items
    .map((item) => item.note || item.reason || item.comment)
    .filter(Boolean)
  return notes.length ? notes.slice(0, 3).join('；') : ''
}

function broadExemptionWarning(row) {
  const observed = issueItems(row.violation_summary, 'observed')
  const exempted = issueItems(row.violation_summary, 'exempted')
  if (exempted.length >= 10) return '豁免数量较大，请审慎签署'
  if (observed.length && exempted.length >= observed.length) {
    return '豁免覆盖全部观察违规，请确认范围'
  }
  return ''
}

async function approveCertification(row, asRole) {
  const fingerprint = row.schema_fingerprint || '—'
  const windowLabel = [
    row.observation_batch_from || '—',
    row.observation_batch_to || '—',
  ].join(' → ')
  const observed = issueItems(row.violation_summary, 'observed')
  const exempted = issueItems(row.violation_summary, 'exempted')
  const warning = broadExemptionWarning(row)
  try {
    await ElMessageBox.confirm(
      [
        `对象 ${row.source_application_id}/${row.object_type} @ ${row.contract_version}`,
        `指纹 ${fingerprint}`,
        `观察窗口 ${windowLabel}`,
        `校验行数 ${row.rows_validated ?? '—'}`,
        `观察违规 ${summarizeCertificationIssues(observed)}`,
        `已豁免 ${summarizeCertificationIssues(exempted)}`,
        exemptionNote(row) ? `豁免说明 ${exemptionNote(row)}` : '豁免说明 —',
        warning ? `警告 ${warning}` : null,
        `全量证据 ${row.full_regression_evidence_ref || '—'}`,
        `增量证据 ${row.incremental_regression_evidence_ref || '—'}`,
        `回滚证据 ${row.rollback_drill_evidence_ref || '—'}`,
      ].filter(Boolean).join('\n'),
      asRole === 'data_owner' ? '确认数据负责人批准' : '确认运维批准',
      { type: warning ? 'error' : 'warning', confirmButtonText: '确认签署', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await ingestApproveCertification(row.certification_id)
  } catch (caught) {
    ElMessage.error(errorText(caught))
    return
  }
  ElMessage.success(asRole === 'data_owner' ? '数据负责人已批准' : '平台运维已批准')
  await loadAll()
}

async function runSync() {
  try {
    const result = await ingestRunSync(actionState.syncMode)
    ElMessage.success(`同步完成：${result.succeeded} 个来源`)
    await loadAll()
  } catch (caught) {
    ElMessage.error(errorText(caught))
    await loadOperationHistory()
  }
}

async function runReconcile() {
  try {
    const result = await ingestRunReconcile()
    driftResult.value = (result.reports || []).filter((report) => report.drifted)
    ElMessage.success(result.drifted ? '一致性检查完成：发现不一致' : '一致性检查完成：全部一致')
    await loadOperationHistory()
  } catch (caught) {
    ElMessage.error(errorText(caught))
    await loadOperationHistory()
  }
}

async function runRebuild(mode) {
  if (!actionState.rebuildSource) {
    ElMessage.warning('请选择要重新同步的数据来源')
    return
  }
  const [sourceApplicationId, objectType] = actionState.rebuildSource.split(':')
  try {
    await ElMessageBox.confirm(
      mode === 'source'
        ? `将从来源应用全量重新拉取 ${actionState.rebuildSource}，期间可能产生大量写入。确认继续？`
        : `将按变更记录重新整理 ${actionState.rebuildSource} 的当前数据。确认继续？`,
      '确认重新同步',
      { type: 'warning', confirmButtonText: '确认执行', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  actionState.rebuilding = true
  try {
    const result = await ingestRunRebuild({ mode, sourceApplicationId, objectType })
    ElMessage.success(`重新同步完成（${result.rebuilt_count ?? result.record_count ?? '—'} 条）`)
    await loadAll()
  } catch (caught) {
    ElMessage.error(errorText(caught))
    await loadOperationHistory()
  } finally {
    actionState.rebuilding = false
  }
}

async function runPrune(apply) {
  if (apply) {
    try {
      await ElMessageBox.confirm('将按当前保留设置删除历史版本，操作不可撤销。确认执行？', '确认清理历史', {
        type: 'warning',
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
  }
  actionState.pruning = true
  try {
    const result = await ingestRunPrune(!apply)
    ElMessage.success(apply ? `清理完成，删除 ${result.deleted} 条` : `预览：将清理 ${result.candidates} 条`)
  } catch (caught) {
    ElMessage.error(errorText(caught))
  } finally {
    actionState.pruning = false
    await loadOperationHistory()
  }
}

const operationLabels = {
  sync: '同步',
  reconcile: '一致性检查',
  rebuild: '重新同步',
  prune: '清理历史',
}

const operationModeLabels = {
  incremental: '增量',
  full: '全量',
  log: '变更记录',
  source: '来源全量',
  apply: '执行',
  'dry-run': '预览',
}

function operationSummary(row) {
  const detail = row.details || {}
  if (row.status === 'FAILED') {
    if (row.action === 'sync' || row.action === 'reconcile') {
      return `成功 ${detail.succeeded ?? detail.sources ?? 0} 个，失败 ${detail.failed ?? 0} 个`
    }
    return detail.error_code ? `失败：${detail.error_code}` : '执行失败'
  }
  if (row.action === 'sync') return `成功同步 ${detail.succeeded ?? 0} 个来源`
  if (row.action === 'reconcile') {
    return `检查 ${detail.sources ?? 0} 个来源 · 不一致 ${detail.drifted ?? 0} 个`
  }
  if (row.action === 'rebuild') {
    const source = [detail.source_application_id, detail.object_type].filter(Boolean).join(' / ')
    return `${source || '数据来源'} · ${detail.record_count ?? '—'} 条`
  }
  return detail.dry_run
    ? `预计清理 ${detail.candidates ?? 0} 条`
    : `已清理 ${detail.deleted ?? 0} 条`
}

onMounted(loadAll)
</script>

<template>
  <div class="page-shell">
    <PageHeader
      title="数据接入"
      description="把各业务应用的数据接入平台，并维护同步、一致性与历史保留；配置以平台为权威。"
    >
      <template #tabs>
        <div class="management-tabs">
          <button
            v-for="tab in tabs"
            :key="tab[0]"
            type="button"
            :class="{ active: activeTab === tab[0] }"
            @click="activeTab = tab[0]"
          >
            {{ tab[1] }}
          </button>
        </div>
      </template>
      <template #actions>
        <el-button @click="loadAll">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
      </template>
    </PageHeader>

    <ApiState :loading="loading" :error="error" :empty="false" @retry="loadAll">
      <div class="metric-grid page-section">
        <MetricCard
          label="数据来源"
          :value="sources.length"
          unit="个"
          hint="已配置的应用数据"
          icon="Connection"
          tone="blue"
        />
        <MetricCard
          label="启用中"
          :value="enabledCount"
          unit="个"
          hint="参与定时同步"
          icon="VideoPlay"
          :tone="enabledCount ? 'green' : 'slate'"
        />
        <MetricCard
          label="保留版本"
          :value="policyForm.retention_keep_versions"
          unit="版/对象"
          :hint="policyForm.retention_keep_days ? `另保留 ${policyForm.retention_keep_days} 天内` : '未启用按天保留'"
          icon="Collection"
          tone="orange"
        />
        <MetricCard
          label="最近同步"
          :value="lastSyncAt ? formatTime(lastSyncAt) : '—'"
          :hint="lastSyncHint"
          icon="Clock"
          :tone="lastSyncAt ? 'blue' : (lastSyncFailedAt ? 'orange' : 'slate')"
        />
      </div>

      <section v-if="activeTab === 'sources'" class="surface-panel page-section">
        <div class="panel-toolbar">
          <strong>数据来源</strong>
          <el-button v-if="canWrite" type="primary" @click="openSourceDialog('create')">
            <el-icon><Plus /></el-icon>新增数据来源
          </el-button>
        </div>
        <el-table :data="sources" style="width: 100%">
          <el-table-column label="应用 / 对象" min-width="200">
            <template #default="scope">
              <strong>{{ scope.row.source_application_id }}</strong>
              <small class="subline mono">{{ scope.row.object_type }}</small>
            </template>
          </el-table-column>
          <el-table-column label="传输" width="110">
            <template #default="scope">
              <span>{{ (scope.row.transport_mode || 'PULL_EXPORT') === 'PUSH_AGENT' ? '推送' : '拉取' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="export_base_url" label="数据地址" min-width="220">
            <template #default="scope">
              <span class="mono">{{ scope.row.export_base_url || (scope.row.transport_mode === 'PUSH_AGENT' ? '入站推送' : '—') }}</span>
            </template>
          </el-table-column>
          <el-table-column label="同步设置" min-width="220">
            <template #default="scope">
              <small v-if="isPushSource(scope.row)">
                水位 {{ scope.row.last_cursor ?? '—' }} · {{ scope.row.last_status || '从未推送' }}
              </small>
              <small v-else>
                每 {{ scope.row.interval_seconds }} 秒 · 回看 {{ scope.row.lookback_versions }} 版 · 每页 {{ scope.row.page_limit }}
              </small>
            </template>
          </el-table-column>
          <el-table-column label="最近同步" width="200">
            <template #default="scope">
              <span>{{ formatTime(scope.row.last_sync_at) }}</span>
              <small
                v-if="scope.row.last_status"
                class="subline"
                :style="{ color: lastStatusOk(scope.row) ? 'var(--ink-500)' : '#b45309' }"
              >{{ lastStatusOk(scope.row) ? '成功' : (isPushSource(scope.row) ? scope.row.last_status : '失败') }}</small>
            </template>
          </el-table-column>
          <el-table-column v-if="canWrite" label="启用" width="90">
            <template #default="scope">
              <el-switch v-model="scope.row.enabled" @change="toggleSource(scope.row)" />
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="scope">
              <StatusTag :status="scope.row.enabled ? 'ACTIVE' : 'DISABLED'" />
            </template>
          </el-table-column>
          <el-table-column v-if="canWrite" label="操作" width="110" fixed="right">
            <template #default="scope">
              <el-button link type="primary" @click="openSourceDialog('edit', scope.row)">编辑</el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <section v-else-if="activeTab === 'contracts'" class="surface-panel page-section contracts-panel">
        <el-form label-width="180px" label-position="left">
          <el-divider content-position="left">登记契约</el-divider>
          <el-form-item label="应用 ID" required>
            <el-input v-model="contractForm.source_application_id" placeholder="如 e10-adapter" />
          </el-form-item>
          <el-form-item label="数据类型" required>
            <el-input v-model="contractForm.object_type" placeholder="如 erp.item" />
          </el-form-item>
          <el-form-item label="契约版本" required>
            <el-input v-model="contractForm.contract_version" placeholder="如 item.v1" />
          </el-form-item>
          <el-form-item label="JSON Schema" required>
            <el-input
              v-model="contractForm.json_schema_text"
              type="textarea"
              :autosize="{ minRows: 8, maxRows: 16 }"
              class="mono"
            />
            <span class="form-hint">仅 DRAFT 可覆盖保存；激活后由数据负责人审核，Push 只接受 ACTIVE 版本。type 必须是 object。</span>
          </el-form-item>
          <el-form-item label="字段分类">
            <el-input
              v-model="contractForm.field_classifications_text"
              type="textarea"
              :autosize="{ minRows: 3, maxRows: 8 }"
              class="mono"
            />
          </el-form-item>
          <el-form-item label="兼容模式">
            <el-select v-model="contractForm.compatibility_mode" style="width: 100%">
              <el-option label="BACKWARD" value="BACKWARD" />
              <el-option label="FORWARD" value="FORWARD" />
              <el-option label="FULL" value="FULL" />
              <el-option label="NONE" value="NONE" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="canWrite">
            <el-button type="primary" @click="saveContractDraft">保存草稿</el-button>
            <el-button @click="inferContractDraft">从 Raw 推导草稿</el-button>
          </el-form-item>
        </el-form>
        <el-table :data="contracts" style="width: 100%">
          <el-table-column prop="source_application_id" label="应用" min-width="140" />
          <el-table-column prop="object_type" label="数据类型" min-width="140" />
          <el-table-column prop="contract_version" label="版本" width="120" />
          <el-table-column label="状态" width="110">
            <template #default="scope">
              <StatusTag :status="scope.row.status" />
            </template>
          </el-table-column>
          <el-table-column label="指纹" min-width="140">
            <template #default="scope">
              <span class="mono">{{ scope.row.schema_fingerprint.slice(0, 12) }}…</span>
            </template>
          </el-table-column>
          <el-table-column label="推导证据" min-width="140">
            <template #default="scope">
              <span class="mono cert-evidence">{{ scope.row.inference_evidence_ref ? '已记录' : '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column v-if="canWrite" label="操作" width="280" fixed="right">
            <template #default="scope">
              <el-button link type="primary" @click="fillContractForm(scope.row)">载入</el-button>
              <el-button
                v-if="scope.row.status === 'DRAFT'"
                link
                type="primary"
                @click="activateContract(scope.row)"
              >激活</el-button>
              <el-button
                v-if="scope.row.status === 'DRAFT'"
                link
                type="danger"
                @click="rejectContract(scope.row)"
              >拒绝</el-button>
              <el-button
                v-if="scope.row.status === 'ACTIVE' || scope.row.status === 'DRAFT'"
                link
                type="primary"
                @click="createCertification(scope.row)"
              >发起认证</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-divider content-position="left">认证（ENFORCE 前置）</el-divider>
        <p class="form-hint">数据负责人与平台运维必须是不同的人，且角色由服务端权限推导，不能在请求里自报。批准前必须有观察批次窗口、空的未豁免违规，以及可追溯的回归/回滚证据。</p>
        <el-form v-if="canWrite" label-width="180px" label-position="left">
          <el-form-item label="校验行数">
            <el-input-number v-model="certEvidence.rows_validated" :min="1" />
          </el-form-item>
          <el-form-item label="观察批次起">
            <el-input v-model="certEvidence.observation_batch_from" placeholder="batch UUID" />
          </el-form-item>
          <el-form-item label="观察批次止">
            <el-input v-model="certEvidence.observation_batch_to" placeholder="batch UUID" />
          </el-form-item>
          <el-form-item label="违规摘要">
            <el-input v-model="certEvidence.violation_summary_text" type="textarea" :rows="4" />
            <span class="form-hint">必须包含空数组 unexempted</span>
          </el-form-item>
          <el-form-item label="豁免摘要">
            <el-input v-model="certEvidence.exemption_summary_text" type="textarea" :rows="3" />
          </el-form-item>
          <el-form-item label="全量回归">
            <el-select v-model="certEvidence.full_regression_status" class="cert-status-select">
              <el-option label="passed" value="passed" />
              <el-option label="failed" value="failed" />
            </el-select>
          </el-form-item>
          <el-form-item label="全量证据">
            <el-input v-model="certEvidence.full_regression_evidence_ref" placeholder="conformance run / 报告 ID" />
          </el-form-item>
          <el-form-item label="增量回归">
            <el-select v-model="certEvidence.incremental_regression_status" class="cert-status-select">
              <el-option label="passed" value="passed" />
              <el-option label="failed" value="failed" />
            </el-select>
          </el-form-item>
          <el-form-item label="增量证据">
            <el-input v-model="certEvidence.incremental_regression_evidence_ref" placeholder="conformance run / 报告 ID" />
          </el-form-item>
          <el-form-item label="回滚演练">
            <el-select v-model="certEvidence.rollback_drill_status" class="cert-status-select">
              <el-option label="passed" value="passed" />
              <el-option label="failed" value="failed" />
            </el-select>
          </el-form-item>
          <el-form-item label="回滚证据">
            <el-input v-model="certEvidence.rollback_drill_evidence_ref" placeholder="演练记录 ID" />
          </el-form-item>
        </el-form>
        <el-table :data="certifications" style="width: 100%">
          <el-table-column prop="source_application_id" label="应用" min-width="140" />
          <el-table-column prop="object_type" label="数据类型" min-width="140" />
          <el-table-column prop="contract_version" label="版本" width="120" />
          <el-table-column label="状态" width="110">
            <template #default="scope">
              <StatusTag :status="scope.row.status" />
            </template>
          </el-table-column>
          <el-table-column prop="data_owner_approved_by" label="数据负责人" min-width="140" />
          <el-table-column prop="operator_approved_by" label="平台运维" min-width="140" />
          <el-table-column label="观察窗口" min-width="220">
            <template #default="scope">
              <small class="mono cert-evidence">{{ scope.row.observation_batch_from || '—' }}</small>
              <small class="mono cert-evidence">{{ scope.row.observation_batch_to || '—' }}</small>
            </template>
          </el-table-column>
          <el-table-column prop="rows_validated" label="校验行数" width="110" />
          <el-table-column label="违规/豁免" min-width="240">
            <template #default="scope">
              <small class="cert-evidence">观察 {{ summarizeCertificationIssues(issueItems(scope.row.violation_summary, 'observed')) }}</small>
              <small class="cert-evidence">豁免 {{ summarizeCertificationIssues(issueItems(scope.row.violation_summary, 'exempted')) }}</small>
              <small v-if="exemptionNote(scope.row)" class="cert-evidence">说明 {{ exemptionNote(scope.row) }}</small>
              <small v-if="broadExemptionWarning(scope.row)" class="cert-warning">{{ broadExemptionWarning(scope.row) }}</small>
            </template>
          </el-table-column>
          <el-table-column label="指纹" min-width="140">
            <template #default="scope">
              <span class="mono cert-evidence">{{ (scope.row.schema_fingerprint || '').slice(0, 12) }}{{ scope.row.schema_fingerprint ? '…' : '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="证据" min-width="220">
            <template #default="scope">
              <small class="cert-evidence">全量 {{ scope.row.full_regression_evidence_ref || '—' }}</small>
              <small class="cert-evidence">增量 {{ scope.row.incremental_regression_evidence_ref || '—' }}</small>
              <small class="cert-evidence">回滚 {{ scope.row.rollback_drill_evidence_ref || '—' }}</small>
            </template>
          </el-table-column>
          <el-table-column v-if="canWrite || canCertify" label="操作" width="200" fixed="right">
            <template #default="scope">
              <el-button
                v-if="canCertifyOwner && scope.row.status === 'DRAFT' && !scope.row.data_owner_approved_by"
                link
                type="primary"
                @click="approveCertification(scope.row, 'data_owner')"
              >负责人批准</el-button>
              <el-button
                v-if="canCertifyOperator && scope.row.status === 'DRAFT' && !scope.row.operator_approved_by"
                link
                type="primary"
                @click="approveCertification(scope.row, 'operator')"
              >运维批准</el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <section v-else-if="activeTab === 'policy'" class="surface-panel page-section policy-panel">
        <el-form label-width="180px" label-position="left">
          <el-divider content-position="left">历史保留</el-divider>
          <el-form-item label="每对象保留版本数">
            <el-input-number v-model="policyForm.retention_keep_versions" :min="1" :max="100000" />
            <span class="form-hint">超出后清理旧版本</span>
          </el-form-item>
          <el-form-item label="按天保留（可选）">
            <el-input-number v-model="policyForm.retention_keep_days" :min="1" :max="3650" placeholder="不启用留空" />
            <span class="form-hint">留空表示不启用第二道闸</span>
          </el-form-item>
          <el-divider content-position="left">批量与负载</el-divider>
          <el-form-item label="每条数据大小上限">
            <el-input-number v-model="policyForm.payload_max_bytes" :min="1024" :max="10485760" :step="1024" />
            <span class="form-hint">当前 {{ formatBytes(policyForm.payload_max_bytes) }}，超限将拒绝写入</span>
          </el-form-item>
          <el-form-item label="默认每页条数">
            <el-input-number v-model="policyForm.page_limit_default" :min="1" :max="policyForm.page_limit_max" />
          </el-form-item>
          <el-form-item label="每页条数上限">
            <el-input-number v-model="policyForm.page_limit_max" :min="1" :max="5000" />
            <span class="form-hint">平台硬上限 5000</span>
          </el-form-item>
          <el-form-item label="推送暂存保留（小时）">
            <el-input-number v-model="policyForm.push_staging_retention_hours" :min="1" :max="168" />
            <span class="form-hint">完成后的推送暂存超过此时长将被清理</span>
          </el-form-item>
          <el-divider content-position="left">一致性与维护</el-divider>
          <el-form-item label="定时一致性检查">
            <el-switch v-model="policyForm.scheduled_reconcile_enabled" />
          </el-form-item>
          <el-form-item label="检查周期（小时）">
            <el-input-number v-model="policyForm.reconcile_interval_hours" :min="1" :max="168" :disabled="!policyForm.scheduled_reconcile_enabled" />
          </el-form-item>
          <el-form-item label="自动重新同步">
            <el-tag type="info" effect="plain">暂不开放：仅支持手动触发</el-tag>
          </el-form-item>
          <el-form-item v-if="canWrite">
            <el-button type="primary" @click="savePolicy">
              <el-icon><Check /></el-icon>保存设置
            </el-button>
          </el-form-item>
        </el-form>
      </section>

      <template v-else>
        <div class="action-grid page-section">
          <section class="surface-panel action-card">
            <h3>立即同步</h3>
            <p>对所有已启用的数据来源执行一次同步。</p>
            <el-radio-group v-model="actionState.syncMode">
              <el-radio-button value="incremental">增量</el-radio-button>
              <el-radio-button value="full">全量</el-radio-button>
            </el-radio-group>
            <div v-if="canWrite" class="action-card__footer">
              <el-button type="primary" @click="runSync">
                <el-icon><Refresh /></el-icon>执行同步
              </el-button>
            </div>
          </section>

          <section class="surface-panel action-card">
            <h3>一致性检查</h3>
            <p>对比来源应用与平台数据，列出不一致项。</p>
            <div v-if="canWrite" class="action-card__footer">
              <el-button type="primary" plain @click="runReconcile">
                <el-icon><DataAnalysis /></el-icon>开始检查
              </el-button>
            </div>
            <div v-if="driftResult && driftResult.length" class="drift-list">
              <div v-for="item in driftResult" :key="`${item.source_application_id}-${item.object_type}`" class="drift-item">
                <strong>{{ item.source_application_id }} / {{ item.object_type }}</strong>
                <small>不一致 {{ item.drift_count }} 项 · 来源 {{ item.expected_count }} · 平台 {{ item.actual_count }}</small>
              </div>
            </div>
            <el-alert v-else-if="driftResult" type="success" :closable="false" title="全部一致" show-icon />
          </section>

          <section class="surface-panel action-card">
            <h3>重新同步</h3>
            <p>修复数据后重新整理；需二次确认，仅支持手动触发。</p>
            <el-select v-model="actionState.rebuildSource" placeholder="选择数据来源" style="width: 100%">
              <el-option
                v-for="item in sources"
                :key="sourceKey(item)"
                :label="`${item.source_application_id} / ${item.object_type}`"
                :value="sourceKey(item)"
              />
            </el-select>
            <p class="form-hint">从来源全量重拉仅适用于拉取模式；推送来源请由中间机发起新的全量 generation。</p>
            <div v-if="canWrite" class="action-card__footer">
              <el-button :loading="actionState.rebuilding" @click="runRebuild('log')">按变更记录重整</el-button>
              <el-button
                type="danger"
                plain
                :loading="actionState.rebuilding"
                :disabled="!pullSources.some((item) => sourceKey(item) === actionState.rebuildSource)"
                @click="runRebuild('source')"
              >
                从来源全量重拉
              </el-button>
            </div>
          </section>

          <section class="surface-panel action-card">
            <h3>清理历史</h3>
            <p>按保留设置删除旧版本；建议先预览再执行。</p>
            <div v-if="canWrite" class="action-card__footer">
              <el-button :loading="actionState.pruning" @click="runPrune(false)">预览</el-button>
              <el-button type="warning" plain :loading="actionState.pruning" @click="runPrune(true)">执行清理</el-button>
            </div>
          </section>
        </div>

        <section class="surface-panel page-section">
          <div class="panel-toolbar"><strong>最近操作</strong></div>
          <el-table :data="runs" style="width: 100%">
            <el-table-column prop="run_id" label="审计记录" min-width="230">
              <template #default="scope"><span class="mono">{{ scope.row.run_id }}</span></template>
            </el-table-column>
            <el-table-column label="操作" width="120">
              <template #default="scope">{{ operationLabels[scope.row.action] || scope.row.action }}</template>
            </el-table-column>
            <el-table-column label="模式" width="110">
              <template #default="scope">{{ operationModeLabels[scope.row.mode] || scope.row.mode || '—' }}</template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="scope"><StatusTag :status="scope.row.status" /></template>
            </el-table-column>
            <el-table-column label="摘要" min-width="260">
              <template #default="scope">{{ operationSummary(scope.row) }}</template>
            </el-table-column>
            <el-table-column label="时间" width="185">
              <template #default="scope">{{ formatTime(scope.row.at) }}</template>
            </el-table-column>
          </el-table>
        </section>
      </template>
    </ApiState>

    <el-drawer
      v-model="sourceDialogVisible"
      :title="sourceDialogMode === 'edit' ? '编辑数据来源' : '新增数据来源'"
      size="min(520px, 96vw)"
    >
      <el-form label-position="top">
        <el-form-item label="应用 ID" required>
          <el-input v-model="sourceForm.source_application_id" :disabled="sourceDialogMode === 'edit'" placeholder="如 order-center" />
        </el-form-item>
        <el-form-item label="数据类型" required>
          <el-input v-model="sourceForm.object_type" :disabled="sourceDialogMode === 'edit'" placeholder="如 order（订单）" />
        </el-form-item>
        <el-form-item label="传输方式" required>
          <el-select v-model="sourceForm.transport_mode" :disabled="transportModeLocked">
            <el-option label="拉取导出（PULL_EXPORT）" value="PULL_EXPORT" />
            <el-option label="中间机推送（PUSH_AGENT）" value="PUSH_AGENT" />
          </el-select>
          <span v-if="sourceDialogMode === 'edit'" class="form-hint">
            {{
              transportModeLocked
                ? '请先停用并保存，等待进行中的同步结束后再改传输方式'
                : '停用后可改传输方式；切换时请保持停用'
            }}
          </span>
        </el-form-item>
        <el-form-item v-if="sourceForm.transport_mode !== 'PUSH_AGENT'" label="数据地址" required>
          <el-input v-model="sourceForm.export_base_url" placeholder="https://…" />
        </el-form-item>
        <el-form-item v-else label="推送协议版本" required>
          <el-input v-model="sourceForm.push_protocol_version" placeholder="1" />
        </el-form-item>
        <el-form-item v-if="sourceForm.transport_mode !== 'PUSH_AGENT'" label="契约校验">
          <el-select v-model="sourceForm.contract_validation_mode">
            <el-option label="只审计，不拒绝（AUDIT_ONLY）" value="AUDIT_ONLY" />
            <el-option label="强制拒绝（ENFORCE）" value="ENFORCE" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="sourceForm.transport_mode === 'PUSH_AGENT'" label="允许空全量">
          <el-switch v-model="sourceForm.allow_empty_full" />
        </el-form-item>
        <el-form-item label="同步间隔（秒）">
          <el-input-number v-model="sourceForm.interval_seconds" :min="10" :max="86400" />
        </el-form-item>
        <el-form-item label="回看版本数">
          <el-input-number v-model="sourceForm.lookback_versions" :min="0" :max="10000" />
        </el-form-item>
        <el-form-item label="每页条数">
          <el-input-number v-model="sourceForm.page_limit" :min="1" :max="policyForm.page_limit_max" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="sourceForm.enabled" :disabled="transportChanging" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="sourceDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveSource">保存</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.management-tabs { display: flex; gap: 24px; }
.management-tabs button { position: relative; height: 46px; padding: 0; border: 0; color: var(--ink-500); background: transparent; font-size: 15px; cursor: pointer; }
.management-tabs button.active { color: var(--ink-900); font-weight: 650; }
.management-tabs button.active::after { position: absolute; right: 0; bottom: 0; left: 0; height: 2px; background: var(--accent-500); content: ''; }
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.panel-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid var(--line);
}
.subline { display: block; margin-top: 4px; color: var(--ink-500); font-size: var(--font-eyebrow); }
.policy-panel { padding: var(--space-card-lg); max-width: 860px; }
.contracts-panel { padding: var(--space-card-lg); max-width: none; }
.form-hint { margin-left: 12px; color: var(--ink-500); font-size: var(--font-caption); }
.cert-status-select { width: 14rem; }
.action-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-gap); }
.action-card { padding: var(--space-card-lg); }
.action-card h3 { margin: 0 0 6px; font-size: var(--font-body-lg); }
.action-card p { margin: 0 0 14px; color: var(--ink-500); font-size: var(--font-body); }
.action-card__footer { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }
.drift-list { display: grid; gap: 8px; margin-top: 14px; }
.drift-item {
  display: grid;
  gap: 3px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fafbfc;
}
.drift-item small { color: var(--ink-500); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.cert-evidence {
  display: block;
  color: var(--ink-500);
  font-size: var(--font-caption);
}
.cert-warning {
  display: block;
  color: var(--danger);
  font-size: var(--font-caption);
}
@media (max-width: 1000px) {
  .metric-grid { grid-template-columns: repeat(2, 1fr); }
  .action-grid { grid-template-columns: 1fr; }
}
@media (max-width: 650px) { .metric-grid { grid-template-columns: 1fr; } .management-tabs { gap: 16px; overflow-x: auto; } }
</style>
