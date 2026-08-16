<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import ApiState from '../components/ApiState.vue'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import {
  mockIngestGetConfig,
  mockIngestListRuns,
  mockIngestRunAction,
  mockIngestSavePolicy,
  mockIngestSaveSource,
  mockIngestSetSourceEnabled,
} from '../services/ingestMock'

const activeTab = ref('sources')
const loading = ref(false)
const error = ref(null)
const sources = ref([])
const runs = ref([])

const tabs = [
  ['sources', '汇聚源'],
  ['policy', '平台策略'],
  ['actions', '运维动作'],
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
  rebuilding: false,
  pruning: false,
})

const driftResult = ref(null)

const enabledCount = computed(() => sources.value.filter((item) => item.enabled).length)
const lastSyncAt = computed(() => {
  const times = sources.value.map((item) => item.last_sync_at).filter(Boolean).sort()
  return times.length ? times[times.length - 1] : null
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
    const config = await mockIngestGetConfig()
    sources.value = config.sources
    Object.assign(policyForm, config.policy)
    runs.value = await mockIngestListRuns()
  } catch (caught) {
    error.value = caught
  } finally {
    loading.value = false
  }
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
    ElMessage.warning('应用 ID、对象类型与导出地址为必填')
    return
  }
  await mockIngestSaveSource({ ...sourceForm })
  sourceDialogVisible.value = false
  ElMessage.success(sourceDialogMode.value === 'edit' ? '汇聚源已保存（原型）' : '汇聚源已创建（原型）')
  await loadAll()
}

async function toggleSource(row) {
  await mockIngestSetSourceEnabled(sourceKey(row), row.enabled)
  ElMessage.success(row.enabled ? '已启用（原型）' : '已停用（原型）')
}

async function savePolicy() {
  await mockIngestSavePolicy({ ...policyForm })
  ElMessage.success('平台策略已保存（原型）')
}

async function runSync() {
  const result = await mockIngestRunAction({ action: 'sync', mode: actionState.syncMode })
  ElMessage.success(result.message || '已提交')
  runs.value = await mockIngestListRuns()
}

async function runReconcile() {
  const result = await mockIngestRunAction({ action: 'reconcile' })
  driftResult.value = result.drift || []
  ElMessage.success(driftResult.value.length ? '对账完成：存在漂移（模拟）' : '对账完成：无漂移')
  runs.value = await mockIngestListRuns()
}

async function runRebuild(mode) {
  try {
    await ElMessageBox.confirm(
      mode === 'source'
        ? '将从源侧全量重建 ODS，期间可能产生大量写操作。确认继续？'
        : '将重建并记录审计日志。确认继续？',
      '确认重建',
      { type: 'warning', confirmButtonText: '确认执行', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  actionState.rebuilding = true
  try {
    const result = await mockIngestRunAction({ action: 'rebuild', mode })
    ElMessage.success(result.message || '已提交')
    runs.value = await mockIngestListRuns()
  } finally {
    actionState.rebuilding = false
  }
}

async function runPrune(apply) {
  if (apply) {
    try {
      await ElMessageBox.confirm('将按当前保留策略删除历史版本，操作不可撤销。确认执行？', '确认裁剪', {
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
    const result = await mockIngestRunAction({ action: 'prune', dry_run: !apply })
    ElMessage.success(apply ? `裁剪完成，删除 ${result.would_delete} 条（原型）` : `预览：将清理 ${result.would_delete} 条（原型）`)
    runs.value = await mockIngestListRuns()
  } finally {
    actionState.pruning = false
  }
}

onMounted(loadAll)
</script>

<template>
  <div class="page-shell">
    <PageHeader
      title="数据汇聚"
      description="原型：所有配置与运维动作暂用本地 Mock，确认交互后再接后端 API。"
    >
      <template #tabs>
        <div class="management-tabs">
          <button
            v-for="tab in tabs"
            :key="tab[0]"
            type="button"
            :class="{ 'is-active': activeTab === tab[0] }"
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
        <el-tag type="warning" effect="plain" round>原型 · Mock 数据</el-tag>
      </template>
    </PageHeader>

    <ApiState :loading="loading" :error="error" :empty="false" @retry="loadAll">
      <div class="metric-grid page-section">
        <MetricCard
          label="汇聚源"
          :value="sources.length"
          unit="个"
          hint="已配置的数据源"
          icon="Connection"
          tone="blue"
        />
        <MetricCard
          label="启用中"
          :value="enabledCount"
          unit="个"
          hint="参与定时拉取"
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
          hint="任一源最近成功"
          icon="Clock"
          tone="blue"
        />
      </div>

      <section v-if="activeTab === 'sources'" class="surface-panel page-section">
        <div class="panel-toolbar">
          <strong>汇聚源</strong>
          <el-button type="primary" @click="openSourceDialog('create')">
            <el-icon><Plus /></el-icon>新增汇聚源
          </el-button>
        </div>
        <el-table :data="sources" style="width: 100%">
          <el-table-column label="应用 / 对象" min-width="200">
            <template #default="scope">
              <strong>{{ scope.row.source_application_id }}</strong>
              <small class="subline mono">{{ scope.row.object_type }}</small>
            </template>
          </el-table-column>
          <el-table-column prop="export_base_url" label="导出地址" min-width="220">
            <template #default="scope">
              <span class="mono">{{ scope.row.export_base_url }}</span>
            </template>
          </el-table-column>
          <el-table-column label="同步参数" min-width="220">
            <template #default="scope">
              <small>
                间隔 {{ scope.row.interval_seconds }}s · 回看 {{ scope.row.lookback_versions }} 版 · 每页 {{ scope.row.page_limit }}
              </small>
            </template>
          </el-table-column>
          <el-table-column label="最近同步" width="180">
            <template #default="scope">{{ formatTime(scope.row.last_sync_at) }}</template>
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
            <span class="form-hint">超出后裁剪旧版本</span>
          </el-form-item>
          <el-form-item label="按天保留（可选）">
            <el-input-number v-model="policyForm.retention_keep_days" :min="1" :max="3650" placeholder="不启用留空" />
            <span class="form-hint">留空表示不启用第二道闸</span>
          </el-form-item>
          <el-divider content-position="left">批量与负载</el-divider>
          <el-form-item label="单条 Payload 上限">
            <el-input-number v-model="policyForm.payload_max_bytes" :min="1024" :max="10485760" :step="1024" />
            <span class="form-hint">当前 {{ formatBytes(policyForm.payload_max_bytes) }}，超限拒绝写入</span>
          </el-form-item>
          <el-form-item label="默认每页条数">
            <el-input-number v-model="policyForm.page_limit_default" :min="1" :max="policyForm.page_limit_max" />
          </el-form-item>
          <el-form-item label="每页条数硬上限">
            <el-input-number v-model="policyForm.page_limit_max" :min="1" :max="50000" />
          </el-form-item>
          <el-divider content-position="left">对账与重建</el-divider>
          <el-form-item label="允许定时对账">
            <el-switch v-model="policyForm.scheduled_reconcile_enabled" />
          </el-form-item>
          <el-form-item label="对账周期（小时）">
            <el-input-number v-model="policyForm.reconcile_interval_hours" :min="1" :max="168" :disabled="!policyForm.scheduled_reconcile_enabled" />
          </el-form-item>
          <el-form-item label="自动重建">
            <el-tag type="info" effect="plain">首期不开放：仅允许手动触发</el-tag>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="savePolicy">
              <el-icon><Check /></el-icon>保存策略
            </el-button>
          </el-form-item>
        </el-form>
      </section>

      <template v-else>
        <div class="action-grid page-section">
          <section class="surface-panel action-card">
            <h3>立即同步</h3>
            <p>对全部启用源执行一次拉取。</p>
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
            <h3>对账</h3>
            <p>比对源侧导出与 ODS，输出漂移清单。</p>
            <div class="action-card__footer">
              <el-button type="primary" plain @click="runReconcile">
                <el-icon><DataAnalysis /></el-icon>执行对账
              </el-button>
            </div>
            <div v-if="driftResult && driftResult.length" class="drift-list">
              <div v-for="item in driftResult" :key="`${item.source_application_id}-${item.object_type}`" class="drift-item">
                <strong>{{ item.source_application_id }} / {{ item.object_type }}</strong>
                <small>ODS 缺 {{ item.missing_in_ods }} · 多 {{ item.extra_in_ods }} · {{ formatTime(item.compared_at) }}</small>
              </div>
            </div>
            <el-alert v-else-if="driftResult" type="success" :closable="false" title="无漂移" show-icon />
          </section>

          <section class="surface-panel action-card">
            <h3>重建</h3>
            <p>手动触发，二次确认；首期不支持自动重建。</p>
            <div class="action-card__footer">
              <el-button :loading="actionState.rebuilding" @click="runRebuild('log')">重建（记日志）</el-button>
              <el-button type="danger" plain :loading="actionState.rebuilding" @click="runRebuild('source')">
                从源侧重建
              </el-button>
            </div>
          </section>

          <section class="surface-panel action-card">
            <h3>历史裁剪</h3>
            <p>按保留策略清理旧版本；建议先 dry-run 预览。</p>
            <div class="action-card__footer">
              <el-button :loading="actionState.pruning" @click="runPrune(false)">预览（dry-run）</el-button>
              <el-button type="warning" plain :loading="actionState.pruning" @click="runPrune(true)">执行裁剪</el-button>
            </div>
          </section>
        </div>

        <section class="surface-panel page-section">
          <div class="panel-toolbar"><strong>最近动作</strong></div>
          <el-table :data="runs" style="width: 100%">
            <el-table-column prop="run_id" label="批次" width="140">
              <template #default="scope"><span class="mono">{{ scope.row.run_id }}</span></template>
            </el-table-column>
            <el-table-column prop="action" label="动作" width="110" />
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
      :title="sourceDialogMode === 'edit' ? '编辑汇聚源' : '新增汇聚源'"
      width="520px"
    >
      <el-form label-width="130px" label-position="left">
        <el-form-item label="应用 ID" required>
          <el-input v-model="sourceForm.source_application_id" :disabled="sourceDialogMode === 'edit'" placeholder="如 order-center" />
        </el-form-item>
        <el-form-item label="对象类型" required>
          <el-input v-model="sourceForm.object_type" :disabled="sourceDialogMode === 'edit'" placeholder="如 order" />
        </el-form-item>
        <el-form-item label="导出地址" required>
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
@media (max-width: 650px) { .metric-grid { grid-template-columns: 1fr; } }
</style>
