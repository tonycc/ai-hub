<script setup>
import { onMounted, reactive, ref } from 'vue'
import ApiState from '../components/ApiState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { apiRequest, queryString } from '../services/platformApi'

const loading = ref(false)
const error = ref(null)
const events = ref([])
const total = ref(0)
const detailVisible = ref(false)
const selected = ref(null)
const filters = reactive({ actor_id: '', action: '', result: '', request_id: '' })

function formatTime(value) { return value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'medium' }).format(new Date(value)) : '—' }
async function loadEvents() {
  loading.value = true; error.value = null
  try {
    const response = await apiRequest(`audit-events${queryString({ ...filters, limit: 100 })}`)
    events.value = response.items; total.value = response.total
  } catch (caught) { error.value = caught } finally { loading.value = false }
}
function showDetail(row) { selected.value = row; detailVisible.value = true }
function clearFilters() { Object.assign(filters, { actor_id: '', action: '', result: '', request_id: '' }); loadEvents() }
onMounted(loadEvents)
</script>

<template><div class="page-shell"><PageHeader title="审计中心" description="查询平台管理操作和公共能力调用；敏感键在服务端递归脱敏。"><el-input v-model="filters.actor_id" clearable placeholder="操作者" style="width:170px" @keyup.enter="loadEvents"/><el-input v-model="filters.action" clearable placeholder="动作" style="width:210px" @keyup.enter="loadEvents"/><el-input v-model="filters.request_id" clearable placeholder="Request ID" style="width:220px" @keyup.enter="loadEvents"/><el-select v-model="filters.result" clearable placeholder="全部结果" style="width:130px"><el-option v-for="item in ['SUCCESS','DENIED','FAILED']" :key="item" :value="item"/></el-select><template #actions><el-button @click="clearFilters">清空</el-button><el-button type="primary" @click="loadEvents"><el-icon><Search/></el-icon>查询</el-button></template></PageHeader><section class="surface-panel page-section list-panel"><ApiState :loading="loading" :error="error" :empty="!events.length" empty-text="没有符合条件的审计记录" @retry="loadEvents"><el-table :data="events" style="width:100%" @row-click="showDetail"><el-table-column prop="occurred_at" label="时间" width="185"><template #default="scope">{{formatTime(scope.row.occurred_at)}}</template></el-table-column><el-table-column prop="actor_id" label="操作者" min-width="190"><template #default="scope"><code>{{scope.row.actor_id||'anonymous'}}</code></template></el-table-column><el-table-column prop="action" label="动作" min-width="240"><template #default="scope"><code>{{scope.row.action}}</code></template></el-table-column><el-table-column prop="application_id" label="应用" min-width="150"><template #default="scope">{{scope.row.application_id||'平台级'}}</template></el-table-column><el-table-column prop="target_id" label="目标" min-width="200"><template #default="scope">{{scope.row.target_id||'—'}}</template></el-table-column><el-table-column prop="request_id" label="Request ID" min-width="210"><template #default="scope"><code>{{scope.row.request_id}}</code></template></el-table-column><el-table-column prop="result" label="结果" width="110"><template #default="scope"><StatusTag :status="scope.row.result"/></template></el-table-column><el-table-column label="操作" width="80" fixed="right"><template #default="scope"><el-button type="primary" link @click.stop="showDetail(scope.row)">详情</el-button></template></el-table-column></el-table><div class="table-footer">共 {{total}} 条，当前显示 {{events.length}} 条</div></ApiState></section><el-drawer v-model="detailVisible" title="审计证据" size="min(620px,96vw)"><el-descriptions v-if="selected" :column="1" border><el-descriptions-item label="审计编号"><code>{{selected.audit_id}}</code></el-descriptions-item><el-descriptions-item label="发生时间">{{formatTime(selected.occurred_at)}}</el-descriptions-item><el-descriptions-item label="操作者">{{selected.actor_type}} · {{selected.actor_id||'anonymous'}}</el-descriptions-item><el-descriptions-item label="动作"><code>{{selected.action}}</code></el-descriptions-item><el-descriptions-item label="目标">{{selected.target_type||'—'}} · {{selected.target_id||'—'}}</el-descriptions-item><el-descriptions-item label="结果"><StatusTag :status="selected.result"/></el-descriptions-item><el-descriptions-item label="错误码">{{selected.error_code||'—'}}</el-descriptions-item><el-descriptions-item label="Request ID"><code>{{selected.request_id}}</code></el-descriptions-item><el-descriptions-item label="Trace ID"><code>{{selected.trace_id||'—'}}</code></el-descriptions-item><el-descriptions-item label="元数据"><pre>{{JSON.stringify(selected.metadata,null,2)}}</pre></el-descriptions-item></el-descriptions></el-drawer></div></template>

<style scoped>.list-panel{min-height:540px;overflow:hidden}.table-footer{padding:13px 16px;color:var(--ink-500);font-size:12px;text-align:right}pre{max-width:100%;margin:0;overflow:auto;font-size:11px;white-space:pre-wrap;word-break:break-all}</style>
