<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import ApiState from '../components/ApiState.vue'
import StatusTag from '../components/StatusTag.vue'
import { apiRequest, queryString } from '../services/platformApi'
import { usePortalSession } from '../stores/session'

const props = defineProps({ application: { type: Object, required: true } })
const session = usePortalSession()
const loading = ref(false)
const error = ref(null)
const runs = ref([])
const applicationId = computed(() => props.application.application_id)
const detailVisible = ref(false)
const selected = ref(null)
const runVisible = ref(false)
const saving = ref(false)
const runForm = reactive({ environment: 'local', profiles: ['OIDC_ONLY'] })
const profileOptions = [
  ['OIDC_ONLY', 'OIDC 身份接入', '登录、基本资料与回调地址'],
  ['API_ONLY', 'API-only', '服务身份、API 范围和入口前置条件'],
  ['DATA_INGEST', '增量数据接入', '导出契约、版本单调、删除捕获与幂等回放证据'],
]
const canRun = computed(() => applicationId.value && session.hasPermission('platform.conformance.run', applicationId.value))

function formatTime(value) {
  return value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—'
}
async function load() {
  loading.value = true
  error.value = null
  try {
    const runResponse = await apiRequest(`conformance-runs${queryString({ application_id: applicationId.value, limit: 100 })}`)
    runs.value = runResponse.items
  } catch (caught) {
    error.value = caught
  } finally {
    loading.value = false
  }
}
async function showDetail(row) {
  try {
    selected.value = await apiRequest(`conformance-runs/${row.run_id}`)
    detailVisible.value = true
  } catch (caught) {
    ElMessage.error(caught.message)
  }
}
function openRun() {
  if (!props.application.environments?.length) { ElMessage.warning('请先登记应用环境'); return }
  const capabilities = props.application?.capabilities || []
  runForm.environment = props.application?.environments?.[0]?.environment || ''
  runForm.profiles = ['OIDC_ONLY']
  if (capabilities.includes('DATA_INGEST')) runForm.profiles.push('DATA_INGEST')
  runVisible.value = true
}
async function runConformance() {
  saving.value = true
  try {
    const response = await apiRequest(`applications/${applicationId.value}/conformance-runs`, {
      method: 'POST',
      body: runForm,
    })
    runVisible.value = false
    selected.value = response
    detailVisible.value = true
    ElMessage.success(response.status === 'PASSED' ? '接入认证通过' : '认证完成，存在未通过项')
    await load()
  } catch (caught) {
    ElMessage.error(caught.message)
  } finally {
    saving.value = false
  }
}
onMounted(load)
</script>

<template>
  <div class="drawer-section">
    <div class="section-heading"><h3>接入检查</h3><div>
        <el-button @click="load">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
        <el-button v-if="canRun" type="primary" @click="openRun">
          <el-icon><VideoPlay /></el-icon>运行认证
        </el-button>
      </div>
    </div>
    <section class="surface-panel page-section list-panel">
      <ApiState :loading="loading" :error="error" :empty="!runs.length" empty-text="尚未运行接入认证" @retry="load">
        <el-table :data="runs" style="width: 100%" @row-click="showDetail">
          <el-table-column prop="environment" label="环境" width="100" />
          <el-table-column label="认证配置" min-width="330">
            <template #default="scope">
              <el-tag
                v-for="profile in scope.row.requested_profiles"
                :key="profile"
                size="small"
                effect="plain"
                class="inline-tag"
              >
                {{ profile }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="contract_version" label="契约版本" min-width="180">
            <template #default="scope"><code>{{ scope.row.contract_version }}</code></template>
          </el-table-column>
          <el-table-column prop="requested_by_name" label="执行人" min-width="150">
            <template #default="scope">{{ scope.row.requested_by_name || '系统' }}</template>
          </el-table-column>
          <el-table-column prop="started_at" label="执行时间" width="175">
            <template #default="scope">{{ formatTime(scope.row.started_at) }}</template>
          </el-table-column>
          <el-table-column prop="status" label="结果" width="110">
            <template #default="scope"><StatusTag :status="scope.row.status" /></template>
          </el-table-column>
          <el-table-column label="操作" width="80" fixed="right">
            <template #default="scope">
              <el-button type="primary" link @click.stop="showDetail(scope.row)">证据</el-button>
            </template>
          </el-table-column>
        </el-table>
      </ApiState>
    </section>
    <el-drawer v-model="runVisible" title="运行接入一致性认证" size="min(640px, 96vw)">
      <el-alert
        type="info"
        :closable="false"
        title="DATA_INGEST 认证只接受应用提交的运行门禁证据，不会跨库读取应用导出表。"
      />
      <el-form label-position="top" class="run-form">
        <el-form-item label="环境" required><el-select v-model="runForm.environment" style="width: 100%"><el-option v-for="item in props.application.environments" :key="item.environment" :value="item.environment" :label="item.environment" /></el-select></el-form-item>
        <el-form-item label="认证配置">
          <el-checkbox-group v-model="runForm.profiles" class="profile-options">
            <el-checkbox v-for="item in profileOptions" :key="item[0]" :value="item[0]">
              <span>
                <strong>{{ item[1] }}</strong>
                <small>{{ item[2] }}</small>
              </span>
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="runVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="runConformance">开始认证</el-button>
      </template>
    </el-drawer>
    <el-drawer v-model="detailVisible" title="认证证据" size="min(720px, 96vw)">
      <template v-if="selected">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="应用">{{ selected.application_name }}</el-descriptions-item>
          <el-descriptions-item label="环境">{{ selected.environment }}</el-descriptions-item>
          <el-descriptions-item label="契约版本"><code>{{ selected.contract_version }}</code></el-descriptions-item>
          <el-descriptions-item label="结果"><StatusTag :status="selected.status" /></el-descriptions-item>
        </el-descriptions>
        <div class="check-list">
          <article v-for="check in selected.checks" :key="check.profile">
            <header>
              <div>
                <strong>{{ check.profile }}</strong>
                <p>{{ check.message }}</p>
              </div>
              <StatusTag :status="check.status" />
            </header>
            <pre>{{ JSON.stringify(check.evidence, null, 2) }}</pre>
          </article>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.profile-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.profile-grid article { display: grid; grid-template-columns: 34px 1fr; gap: 10px; padding: 16px; }
.profile-grid > .surface-panel > .el-icon {
  display: grid; width: 32px; height: 32px; border-radius: 7px; color: var(--success); background: #eaf4ef; place-items: center;
}
.profile-grid div { display: grid; gap: 4px; }
.profile-grid strong { font-size: 13px; }
.profile-grid code { color: var(--accent-600); font-size: 10px; }
.profile-grid p { margin: 2px 0 0; color: var(--ink-500); font-size: 11px; line-height: 1.5; }
.list-panel { min-height: 480px; overflow: hidden; }
.subline { display: block; margin-top: 4px; color: var(--ink-500); font-size: 11px; }
.inline-tag { margin: 2px 4px 2px 0; }
.run-form { margin-top: 18px; }
.profile-options { display: grid; gap: 10px; }
.profile-options :deep(.el-checkbox) {
  height: auto; margin: 0; padding: 10px; border: 1px solid var(--line); border-radius: 6px;
}
.profile-options span { display: grid; gap: 3px; }
.profile-options small { color: var(--ink-500); font-size: 11px; }
.check-list { display: grid; gap: 12px; margin-top: 20px; }
.check-list article { border: 1px solid var(--line); border-radius: 7px; overflow: hidden; }
.check-list header {
  display: flex; justify-content: space-between; gap: 12px; padding: 13px; background: #f7f9fa;
}
.check-list header p { margin: 5px 0 0; color: var(--ink-500); font-size: 12px; }
.check-list pre {
  max-height: 220px; margin: 0; padding: 13px; overflow: auto; background: #202b35; color: #dce5ea;
  font-size: 11px; white-space: pre-wrap; word-break: break-all;
}
@media (max-width: 650px) { .profile-grid { grid-template-columns: 1fr; } }
</style>
