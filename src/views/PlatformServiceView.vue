<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { platformServices } from '../data/platformCapabilities'

const route = useRoute()
const router = useRouter()
const keyword = ref('')
const activeSectionKey = ref('')
const detailVisible = ref(false)
const selectedRow = ref(null)

const service = computed(() => platformServices[route.params.service] || platformServices.identity)
const activeSection = computed(() => service.value.sections.find((section) => section.key === activeSectionKey.value) || service.value.sections[0])
const filteredRows = computed(() => {
  const key = keyword.value.trim().toLowerCase()
  if (!key) return activeSection.value.rows
  return activeSection.value.rows.filter((row) => Object.values(row).join(' ').toLowerCase().includes(key))
})

function syncSectionFromRoute() {
  const requested = route.query.tab
  activeSectionKey.value = service.value.sections.some((section) => section.key === requested) ? requested : service.value.sections[0].key
  keyword.value = ''
}

watch(() => [route.params.service, route.query.tab], syncSectionFromRoute, { immediate: true })

function changeSection(key) {
  activeSectionKey.value = key
  keyword.value = ''
  router.replace({ query: key === service.value.sections[0].key ? {} : { tab: key } })
}

function showDetail(row) {
  selectedRow.value = row
  detailVisible.value = true
}

function runAction(label) {
  ElMessage.success(`${label}已进入处理队列（原型演示）`)
}

function cellText(value) {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}
</script>

<template>
  <div class="page-shell service-page">
    <PageHeader :eyebrow="service.eyebrow" :title="service.title" :description="service.description">
      <template #tabs>
        <div class="service-tabs">
          <button v-for="section in service.sections" :key="section.key" type="button" :class="{ active: activeSection.key === section.key }" @click="changeSection(section.key)">
            {{ section.label }}<em>{{ section.rows.length }}</em>
          </button>
        </div>
      </template>
      <el-input v-model="keyword" clearable prefix-icon="Search" :placeholder="`搜索${activeSection.label}`" style="width: 250px" />
      <template #actions>
        <el-button><el-icon><Document /></el-icon>服务规范</el-button>
        <el-button @click="runAction(activeSection.action)"><el-icon><Plus /></el-icon>{{ activeSection.action }}</el-button>
        <el-button type="primary" @click="runAction(service.primaryAction)"><el-icon><Plus /></el-icon>{{ service.primaryAction }}</el-button>
      </template>
    </PageHeader>

    <div class="metric-grid page-section">
      <MetricCard v-for="metric in service.metrics" :key="metric.label" v-bind="metric" />
    </div>

    <section class="surface-panel service-workbench page-section">
      <el-table :data="filteredRows" style="width: 100%" @row-click="showDetail">
        <el-table-column
          v-for="column in activeSection.columns"
          :key="column.field"
          :prop="column.field"
          :label="column.label"
          :width="column.width"
          :min-width="column.minWidth"
        >
          <template #default="scope">
            <StatusTag v-if="column.type === 'status'" :status="cellText(scope.row[column.field])" />
            <el-tag v-else-if="column.type === 'tag'" size="small" type="info" effect="plain">{{ cellText(scope.row[column.field]) }}</el-tag>
            <span v-else-if="column.type === 'mono'" class="mono mono-cell">{{ cellText(scope.row[column.field]) }}</span>
            <el-progress v-else-if="column.type === 'progress'" :percentage="Number(scope.row[column.field])" :stroke-width="5" />
            <span v-else>{{ cellText(scope.row[column.field]) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="84" fixed="right"><template #default="scope"><el-button type="primary" link @click.stop="showDetail(scope.row)">查看</el-button></template></el-table-column>
      </el-table>

      <div class="table-footer"><span>共 {{ filteredRows.length }} 条原型数据</span><el-pagination background layout="prev, pager, next" :total="filteredRows.length" :page-size="10" /></div>
    </section>

    <el-drawer v-model="detailVisible" :title="`${activeSection.label}详情`" size="min(520px, 96vw)">
      <template v-if="selectedRow">
        <div class="record-detail-header" :style="{ '--service-tone': service.tone }"><span><el-icon><component :is="service.icon" /></el-icon></span><div><small>{{ service.title }}</small><h2>{{ selectedRow.name || selectedRow.title || selectedRow.businessNo || selectedRow.id || '记录详情' }}</h2><code>{{ selectedRow.code || selectedRow.requestId || selectedRow.traceId || selectedRow.runId || '' }}</code></div></div>
        <el-descriptions :column="1" border>
          <el-descriptions-item v-for="column in activeSection.columns" :key="column.field" :label="column.label">
            <StatusTag v-if="column.type === 'status'" :status="cellText(selectedRow[column.field])" />
            <span v-else :class="{ mono: column.type === 'mono' }">{{ cellText(selectedRow[column.field]) }}</span>
          </el-descriptions-item>
        </el-descriptions>
        <div class="record-audit"><span>治理信息</span><dl><div><dt>数据归属</dt><dd>{{ service.title }}公共服务</dd></div><div><dt>最后变更</dt><dd>当前原型会话</dd></div><div><dt>Request ID</dt><dd class="mono">req-prototype-20260810</dd></div><div><dt>审计状态</dt><dd>已记录</dd></div></dl></div>
      </template>
      <template #footer><el-button @click="detailVisible = false">关闭</el-button><el-button type="primary" @click="runAction('保存')">编辑并保存</el-button></template>
    </el-drawer>
  </div>
</template>

<style scoped>
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.service-workbench { min-height: 500px; overflow: hidden; }
.service-tabs { display: flex; flex: 1 1 100%; width: 100%; min-width: 0; flex-wrap: wrap; gap: 0 24px; overflow: visible; }
.service-tabs button { position: relative; flex: none; height: 46px; padding: 0 1px; border: 0; color: var(--ink-500); background: transparent; font-size: 16px; cursor: pointer; }
.service-tabs button.active { color: var(--ink-900); font-weight: 650; }
.service-tabs button.active::after { position: absolute; right: 0; bottom: -1px; left: 0; height: 2px; background: var(--accent-500); content: ""; }
.service-tabs em { margin-left: 5px; color: #929da4; font-size: 11px; font-style: normal; }
.mono-cell { color: #426b80; font-size: 11px; }
.table-footer { display: flex; align-items: center; justify-content: space-between; padding: 13px 18px; border-top: 1px solid #edf0f2; color: var(--ink-500); font-size: 11px; }
.record-detail-header { display: flex; align-items: flex-start; gap: 11px; margin-bottom: 18px; }
.record-detail-header > span { display: grid; flex: 0 0 42px; width: 42px; height: 42px; border-radius: 8px; color: #fff; background: var(--service-tone); font-size: 19px; place-items: center; }
.record-detail-header > div { display: grid; gap: 3px; }
.record-detail-header small { color: var(--ink-500); font-size: 11px; }
.record-detail-header h2 { margin: 0; color: var(--ink-900); font-size: 16px; }
.record-detail-header code { color: #70808a; font-size: 11px; }
.record-audit { margin-top: 20px; }
.record-audit > span { color: var(--ink-500); font-size: 11px; font-weight: 700; }
.record-audit dl { margin-top: 8px; border-top: 1px solid #e7ebed; }
.record-audit dl div { display: flex; justify-content: space-between; gap: 12px; padding: 9px 0; border-bottom: 1px solid #edf0f2; }
.record-audit dt, .record-audit dd { margin: 0; color: var(--ink-500); font-size: 11px; }
.record-audit dd { color: var(--ink-700); }
@media (max-width: 1000px) { .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 720px) { .page-header__filters :deep(.el-input), .page-header__filters :deep(.el-select) { width: 100% !important; } }
@media (max-width: 520px) { .metric-grid { grid-template-columns: 1fr; } }
</style>
