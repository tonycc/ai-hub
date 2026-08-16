<script setup>
import { computed, onMounted, ref } from 'vue'
import ApiState from '../components/ApiState.vue'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { apiRequest } from '../services/platformApi'

const loading = ref(false)
const error = ref(null)
const summary = ref(null)
const warningCount = computed(() => summary.value
  ? summary.value.application_entries.filter((item) => ['WARNING', 'CRITICAL'].includes(item.status)).length
  : 0)
function formatTime(value) {
  return value
    ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'medium' }).format(new Date(value))
    : '—'
}
async function load() {
  loading.value = true
  error.value = null
  try {
    summary.value = await apiRequest('operations/summary')
  } catch (caught) {
    error.value = caught
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<template>
  <div class="page-shell">
    <PageHeader
      title="运维中心"
      description="用只读指标查看应用入口健康；此页面不提供应用配置写操作。"
    >
      <template #actions>
        <el-button @click="$router.push('/platform/developer')">
          <el-icon><Document /></el-icon>运行手册
        </el-button>
        <el-button type="primary" @click="load">
          <el-icon><Refresh /></el-icon>刷新诊断
        </el-button>
      </template>
    </PageHeader>
    <ApiState :loading="loading" :error="error" :empty="!summary" @retry="load">
      <template v-if="summary">
        <div class="metric-grid page-section">
          <MetricCard
            label="整体状态"
            :value="summary.overall_status === 'HEALTHY' ? '健康' : '降级'"
            hint="平台只读诊断"
            icon="Monitor"
            :tone="summary.overall_status === 'HEALTHY' ? 'green' : 'amber'"
          />
          <MetricCard
            label="异常对象"
            :value="warningCount"
            unit="项"
            hint="WARNING / CRITICAL"
            icon="Warning"
            :tone="warningCount ? 'amber' : 'green'"
          />
          <MetricCard
            label="应用入口"
            :value="summary.application_entries.length"
            unit="个"
            hint="已登记环境"
            icon="Connection"
            tone="blue"
          />
          <MetricCard
            label="观测时间"
            :value="new Date(summary.observed_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })"
            hint="以服务端时钟为准"
            icon="Clock"
            tone="blue"
          />
        </div>
        <section class="surface-panel page-section list-panel">
          <el-table :data="summary.application_entries" style="width: 100%">
            <el-table-column prop="application_name" label="应用" min-width="190">
              <template #default="scope">
                <strong>{{ scope.row.application_name }}</strong>
                <small class="subline mono">{{ scope.row.application_id }} · {{ scope.row.environment }}</small>
              </template>
            </el-table-column>
            <el-table-column prop="portal_url" label="入口" min-width="250">
              <template #default="scope">
                <a :href="scope.row.portal_url" target="_blank">{{ scope.row.portal_url }}</a>
              </template>
            </el-table-column>
            <el-table-column prop="last_health_checked_at" label="最近检查" width="185">
              <template #default="scope">{{ formatTime(scope.row.last_health_checked_at) }}</template>
            </el-table-column>
            <el-table-column prop="reason" label="诊断" min-width="290" />
            <el-table-column prop="status" label="状态" width="115">
              <template #default="scope"><StatusTag :status="scope.row.status" /></template>
            </el-table-column>
          </el-table>
        </section>
      </template>
    </ApiState>
  </div>
</template>

<style scoped>
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.list-panel { min-height: 500px; overflow: hidden; }
.subline { display: block; margin-top: 4px; color: var(--ink-500); font-size: 11px; }
a { color: var(--accent-600); word-break: break-all; }
@media (max-width: 1000px) { .metric-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 650px) { .metric-grid { grid-template-columns: 1fr; } }
</style>
