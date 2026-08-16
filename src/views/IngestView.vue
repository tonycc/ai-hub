<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import ApiState from '../components/ApiState.vue'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import {
  ingestGetConfig,
  ingestRunPrune,
  ingestRunRebuild,
  ingestRunReconcile,
  ingestRunSync,
  ingestSavePolicy,
  ingestSaveSource,
} from '../services/ingestApi'

const activeTab = ref('sources')
const loading = ref(false)
const error = ref(null)
const sources = ref([])
const runs = ref([])

function pushRun(entry) {
  runs.value.unshift({
    run_id: `run-${Date.now()}`,
    at: new Date().toISOString(),
    ...entry,
  })
}

const tabs = [
  ['sources', '数据来源'],
  ['policy', '同步与保留设置'],
  ['actions', '同步与维护'],
]

const policyForm = reactive({
  retention_keep_versions: 100,
  retention_keep_days: null,
  payload_max_bytes: 1048576,
  page_limit_default: 200,
  page_limit_max: 5000,
  scheduled_reconcile_enabled: false,
  reconcile_interval_hours: 24,
})

const sourceDialogVisible = ref(false)
const sourceDialogMode = ref('create')
const sourceForm = reactive({
  source_application_id: '',
  object_type: '',
  export_base_url: '',
  interval_seconds: 60,
  lookback_versions: 100,
  page_limit: 200,
  enabled: false,
})

const actionState = reactive({
  syncMode: 'incremental',
  rebuildSource: '',
  rebuilding: false,
  pruning: false,
})

const driftResult = ref(null)

const enabledCount = computed(() => sources.value.filter((item) => item.enabled).length)
const lastSyncAt = computed(() => {
  const times = sources.value
    .filter((item) => item.last_status === 'ok' && item.last_sync_at)
    .map((item) => item.last_sync_at)
    .sort()
  return times.length ? times[times.length - 1] : null
})
const lastSyncFailedAt = computed(() => {
  const times = sources.value
    .filter((item) => item.last_status === 'failed' && item.last_sync_at)
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

async function loadAll() {
  loading.value = true
  error.value = null
  try {
    const config = await ingestGetConfig()
    sources.value = config.sources
    Object.assign(policyForm, config.policy)
    if (!actionState.rebuildSource && config.sources.length) {
      actionState.rebuildSource = sourceKey(config.sources[0])
    }
  } catch (caught) {
    error.value = caught
  } finally {
    loading.value = false
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
  if (mode === 'edit' && row) Object.assign(sourceForm, row)
  else Object.assign(sourceForm, {
    source_application_id: '',
    object_type: '',
    export_base_url: '',
    interval_seconds: 60,
    lookback_versions: 100,
    page_limit: 200,
    enabled: false,
  })
  sourceDialogVisible.value = true
}

async function saveSource() {
  if (!sourceForm.source_application_id || !sourceForm.object_type || !sourceForm.export_base_url) {
    ElMessage.warning('应用 ID、数据类型与数据地址为必填')
    return
  }
  try {
    await ingestSaveSource({ ...sourceForm })
  } catch (caught) {
    ElMessage.error(errorText(caught))
    return
  }
  sourceDialogVisible.value = false
  ElMessage.success(sourceDialogMode.value === 'edit' ? '数据来源已保存' : '数据来源已创建')
  await loadAll()
}

async function toggleSource(row) {
  try {
    await ingestSaveSource({ ...row })
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

async function runSync() {
  try {
    const result = await ingestRunSync(actionState.syncMode)
    pushRun({ action: 'sync', mode: actionState.syncMode, summary: `成功 ${result.succeeded} 个来源` })
    ElMessage.success(`同步完成：${result.succeeded} 个来源`)
    await loadAll()
  } catch (caught) {
    pushRun({ action: 'sync', mode: actionState.syncMode, summary: `失败：${errorText(caught)}` })
    ElMessage.error(errorText(caught))
  }
}

async function runReconcile() {
  try {
    const result = await ingestRunReconcile()
    driftResult.value = (result.reports || []).filter((report) => report.drifted)
    pushRun({
      action: 'reconcile',
      mode: '-',
      summary: `检查 ${result.sources} 个来源 · 不一致 ${result.drifted} 个`,
    })
    ElMessage.success(result.drifted ? '一致性检查完成：发现不一致' : '一致性检查完成：全部一致')
  } catch (caught) {
    ElMessage.error(errorText(caught))
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
    pushRun({ action: 'rebuild', mode, summary: `重新同步 ${actionState.rebuildSource} 完成` })
    ElMessage.success(`重新同步完成（${result.rebuilt_count ?? result.record_count ?? '—'} 条）`)
    await loadAll()
  } catch (caught) {
    pushRun({ action: 'rebuild', mode, summary: `失败：${errorText(caught)}` })
    ElMessage.error(errorText(caught))
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
    pushRun({
      action: 'prune',
      mode: apply ? 'apply' : 'dry-run',
      summary: apply ? `已清理 ${result.deleted} 条` : `预览：将清理 ${result.candidates} 条`,
    })
    ElMessage.success(apply ? `清理完成，删除 ${result.deleted} 条` : `预览：将清理 ${result.candidates} 条`)
  } catch (caught) {
    ElMessage.error(errorText(caught))
  } finally {
    actionState.pruning = false
  }
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
          <el-button type="primary" @click="openSourceDialog('create')">
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
          <el-table-column prop="export_base_url" label="数据地址" min-width="220">
            <template #default="scope">
              <span class="mono">{{ scope.row.export_base_url }}</span>
            </template>
          </el-table-column>
          <el-table-column label="同步设置" min-width="220">
            <template #default="scope">
              <small>
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
                :style="{ color: scope.row.last_status === 'ok' ? 'var(--ink-500)' : '#b45309' }"
              >{{ scope.row.last_status === 'ok' ? '成功' : '失败' }}</small>
            </template>
          </el-table-column>
          <el-table-column label="启用" width="90">
            <template #default="scope">
              <el-switch v-model="scope.row.enabled" @change="toggleSource(scope.row)" />
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="scope">
              <StatusTag :status="scope.row.enabled ? 'ACTIVE' : 'DISABLED'" />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="110" fixed="right">
            <template #default="scope">
              <el-button link type="primary" @click="openSourceDialog('edit', scope.row)">编辑</el-button>
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
            <el-input-number v-model="policyForm.page_limit_max" :min="1" :max="50000" />
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
          <el-form-item>
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
            <div class="action-card__footer">
              <el-button type="primary" @click="runSync">
                <el-icon><Refresh /></el-icon>执行同步
              </el-button>
            </div>
          </section>

          <section class="surface-panel action-card">
            <h3>一致性检查</h3>
            <p>对比来源应用与平台数据，列出不一致项。</p>
            <div class="action-card__footer">
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
            <div class="action-card__footer">
              <el-button :loading="actionState.rebuilding" @click="runRebuild('log')">按变更记录重整</el-button>
              <el-button type="danger" plain :loading="actionState.rebuilding" @click="runRebuild('source')">
                从来源全量重拉
              </el-button>
            </div>
          </section>

          <section class="surface-panel action-card">
            <h3>清理历史</h3>
            <p>按保留设置删除旧版本；建议先预览再执行。</p>
            <div class="action-card__footer">
              <el-button :loading="actionState.pruning" @click="runPrune(false)">预览</el-button>
              <el-button type="warning" plain :loading="actionState.pruning" @click="runPrune(true)">执行清理</el-button>
            </div>
          </section>
        </div>

        <section class="surface-panel page-section">
          <div class="panel-toolbar"><strong>最近操作</strong></div>
          <el-table :data="runs" style="width: 100%">
            <el-table-column prop="run_id" label="批次" width="140">
              <template #default="scope"><span class="mono">{{ scope.row.run_id }}</span></template>
            </el-table-column>
            <el-table-column prop="action" label="操作" width="110" />
            <el-table-column prop="mode" label="模式" width="110" />
            <el-table-column prop="summary" label="摘要" min-width="260" />
            <el-table-column label="时间" width="185">
              <template #default="scope">{{ formatTime(scope.row.at) }}</template>
            </el-table-column>
          </el-table>
        </section>
      </template>
    </ApiState>

    <el-dialog
      v-model="sourceDialogVisible"
      :title="sourceDialogMode === 'edit' ? '编辑数据来源' : '新增数据来源'"
      width="520px"
    >
      <el-form label-width="130px" label-position="left">
        <el-form-item label="应用 ID" required>
          <el-input v-model="sourceForm.source_application_id" :disabled="sourceDialogMode === 'edit'" placeholder="如 order-center" />
        </el-form-item>
        <el-form-item label="数据类型" required>
          <el-input v-model="sourceForm.object_type" :disabled="sourceDialogMode === 'edit'" placeholder="如 order（订单）" />
        </el-form-item>
        <el-form-item label="数据地址" required>
          <el-input v-model="sourceForm.export_base_url" placeholder="https://…" />
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
          <el-switch v-model="sourceForm.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="sourceDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveSource">保存</el-button>
      </template>
    </el-dialog>
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
.subline { display: block; margin-top: 4px; color: var(--ink-500); font-size: 11px; }
.policy-panel { padding: 18px 22px 8px; max-width: 860px; }
.form-hint { margin-left: 12px; color: var(--ink-500); font-size: 12px; }
.action-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.action-card { padding: 18px; }
.action-card h3 { margin: 0 0 6px; font-size: 15px; }
.action-card p { margin: 0 0 14px; color: var(--ink-500); font-size: 13px; }
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
@media (max-width: 1000px) {
  .metric-grid { grid-template-columns: repeat(2, 1fr); }
  .action-grid { grid-template-columns: 1fr; }
}
@media (max-width: 650px) { .metric-grid { grid-template-columns: 1fr; } .management-tabs { gap: 16px; overflow-x: auto; } }
</style>
