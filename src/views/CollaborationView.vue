<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { usePrototypeStore } from '../stores/prototype'

const store = usePrototypeStore()
const activeTab = ref('capabilities')
const keyword = ref('')
const typeFilter = ref('全部类型')
const detailVisible = ref(false)
const selectedCapability = ref(null)

const filteredCapabilities = computed(() => {
  const key = keyword.value.trim().toLowerCase()
  return store.state.capabilities.filter((item) => {
    const matchesKey = !key || `${item.code}${item.name}${item.provider}`.toLowerCase().includes(key)
    const matchesType = typeFilter.value === '全部类型' || item.kind === typeFilter.value
    return matchesKey && matchesType
  })
})

function showCapability(item) {
  selectedCapability.value = item
  detailVisible.value = true
}

function retry(row) {
  store.retryCommand(row.id)
  ElMessage.success(`${row.id} 已重试成功`)
}
</script>

<template>
  <div class="page-shell collaboration-page">
    <PageHeader title="接入治理" description="统一治理公开 API、服务身份、scope、事件能力和只读投影，不共享数据库内部实现。">
      <template #tabs>
        <div class="collaboration-tabs">
          <button :class="{ active: activeTab === 'capabilities' }" @click="activeTab = 'capabilities'">公开契约目录</button>
          <button :class="{ active: activeTab === 'commands' }" @click="activeTab = 'commands'">认证命令记录 <em>1</em></button>
          <button :class="{ active: activeTab === 'events' }" @click="activeTab = 'events'">事件订阅</button>
          <button :class="{ active: activeTab === 'identities' }" @click="activeTab = 'identities'">服务身份</button>
        </div>
      </template>
      <template v-if="activeTab === 'capabilities'">
        <el-input v-model="keyword" clearable prefix-icon="Search" placeholder="搜索能力编码、名称或提供应用" style="width: 320px" />
        <el-select v-model="typeFilter" style="width: 130px">
          <el-option v-for="item in ['全部类型', 'QUERY', 'COMMAND', 'EVENT']" :key="item" :label="item" :value="item" />
        </el-select>
        <span class="toolbar-count">{{ filteredCapabilities.length }} 项已注册能力</span>
      </template>
      <template #actions>
        <el-button><el-icon><Document /></el-icon>契约规范</el-button>
        <el-button v-if="activeTab === 'commands'"><el-icon><Refresh /></el-icon>刷新状态</el-button>
        <el-button type="primary"><el-icon><Plus /></el-icon>注册公开能力</el-button>
      </template>
    </PageHeader>

    <div class="metric-grid page-section">
      <MetricCard label="登记能力" :value="store.state.capabilities.length" unit="项" hint="中性接入契约" icon="Connection" tone="blue" />
      <MetricCard label="认证调用" value="0" unit="次" hint="等待 M1 身份链路" icon="DataLine" tone="green" />
      <MetricCard label="事件配置" value="1" unit="套" hint="默认保持未启用" icon="CircleCheck" tone="green" />
      <MetricCard label="待验证命令" :value="store.state.commands.length" unit="项" hint="仅中性测试记录" icon="Warning" tone="amber" />
    </div>

    <section class="surface-panel collaboration-panel page-section">
      <template v-if="activeTab === 'capabilities'">
        <el-table :data="filteredCapabilities" @row-click="showCapability">
          <el-table-column label="能力名称" min-width="230">
            <template #default="scope"><div class="capability-cell"><strong>{{ scope.row.name }}</strong><span class="mono">{{ scope.row.code }}</span></div></template>
          </el-table-column>
          <el-table-column prop="provider" label="提供应用" width="130" />
          <el-table-column label="类型" width="105"><template #default="scope"><el-tag :type="scope.row.kind === 'COMMAND' ? 'warning' : scope.row.kind === 'EVENT' ? 'success' : 'info'" size="small" effect="plain">{{ scope.row.kind }}</el-tag></template></el-table-column>
          <el-table-column prop="version" label="当前版本" width="100" />
          <el-table-column label="消费应用" min-width="180"><template #default="scope"><span class="consumer-list">{{ scope.row.consumers.join('、') }}</span></template></el-table-column>
          <el-table-column label="状态" width="90"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column>
          <el-table-column prop="p95" label="P95" width="85" />
          <el-table-column label="操作" width="80"><template #default="scope"><el-button type="primary" link @click.stop="showCapability(scope.row)">详情</el-button></template></el-table-column>
        </el-table>
      </template>

      <template v-else-if="activeTab === 'commands'">
        <el-table :data="store.state.commands">
          <el-table-column prop="id" label="命令编号" width="175"><template #default="scope"><span class="mono command-id">{{ scope.row.id }}</span></template></el-table-column>
          <el-table-column prop="capability" label="业务能力" min-width="160" />
          <el-table-column label="调用链路" min-width="200"><template #default="scope"><span>{{ scope.row.source }}</span><el-icon class="route-arrow"><Right /></el-icon><span>{{ scope.row.target }}</span></template></el-table-column>
          <el-table-column label="状态" width="105"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column>
          <el-table-column prop="attempts" label="尝试次数" width="90" />
          <el-table-column prop="time" label="发起时间" width="100" />
          <el-table-column prop="message" label="最后结果" min-width="240" />
          <el-table-column label="操作" width="90"><template #default="scope"><el-button v-if="scope.row.status === 'RETRYING'" type="primary" link @click="retry(scope.row)">人工重试</el-button><el-button v-else link>查看</el-button></template></el-table-column>
        </el-table>
      </template>

      <el-empty v-else description="该能力将在后续版本完善，当前原型用于确认导航和治理边界" />
    </section>

    <el-drawer v-model="detailVisible" title="公开能力详情" size="min(520px, 96vw)">
      <template v-if="selectedCapability">
        <div class="capability-detail-title"><span><el-icon><Connection /></el-icon></span><div><small>{{ selectedCapability.kind }}</small><h2>{{ selectedCapability.name }}</h2><code>{{ selectedCapability.code }}</code></div></div>
        <el-descriptions :column="1" border class="capability-descriptions">
          <el-descriptions-item label="提供应用">{{ selectedCapability.provider }}</el-descriptions-item>
          <el-descriptions-item label="当前版本">{{ selectedCapability.version }}</el-descriptions-item>
          <el-descriptions-item label="消费应用">{{ selectedCapability.consumers.join('、') }}</el-descriptions-item>
          <el-descriptions-item label="运行状态"><StatusTag :status="selectedCapability.status" /></el-descriptions-item>
          <el-descriptions-item label="服务身份">svc.standalone-reference</el-descriptions-item>
          <el-descriptions-item label="对象类型">example.record</el-descriptions-item>
        </el-descriptions>
        <div class="contract-example">
          <span>契约摘要</span>
          <pre>{
  "input": { "record_id": "string" },
  "output": { "$ref": "example.record@1" },
  "idempotency": "required_for_command"
}</pre>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.collaboration-panel { min-height: 440px; overflow: hidden; }
.collaboration-tabs { display: flex; flex: 1 1 100%; width: 100%; min-width: 0; flex-wrap: wrap; gap: 0 24px; overflow: visible; }
.collaboration-tabs button { position: relative; flex: none; height: 46px; padding: 0 1px; border: 0; color: var(--ink-500); background: transparent; font-size: 16px; cursor: pointer; }
.collaboration-tabs button.active { color: var(--ink-900); font-weight: 650; }
.collaboration-tabs button.active::after { position: absolute; right: 0; bottom: -1px; left: 0; height: 2px; background: var(--accent-500); content: ""; }
.collaboration-tabs em { display: inline-block; min-width: 19px; margin-left: 5px; padding: 2px 5px; border-radius: 9px; color: var(--accent-600); background: var(--accent-100); font-size: 11px; font-style: normal; }
.toolbar-count { flex: none; color: var(--ink-500); font-size: 11px; white-space: nowrap; }
.capability-cell { display: grid; gap: 4px; cursor: pointer; }
.capability-cell strong { color: var(--ink-900); font-size: 13px; }
.capability-cell span { color: #73838d; font-size: 11px; }
.consumer-list { color: var(--ink-700); font-size: 12px; }
.command-id { color: #3e667c; font-size: 11px; }
.route-arrow { margin: 0 6px; color: #9ca7ad; vertical-align: -2px; }
.capability-detail-title { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 20px; }
.capability-detail-title > span { display: grid; width: 42px; height: 42px; border-radius: 8px; color: #fff; background: var(--brand-800); font-size: 19px; place-items: center; }
.capability-detail-title > div { display: grid; gap: 4px; }
.capability-detail-title small { color: var(--accent-600); font-size: 11px; font-weight: 700; }
.capability-detail-title h2 { margin: 0; color: var(--ink-900); font-size: 17px; }
.capability-detail-title code { color: var(--ink-500); font-size: 11px; }
.capability-descriptions { margin-top: 14px; }
.contract-example { margin-top: 20px; }
.contract-example > span { color: var(--ink-500); font-size: 11px; font-weight: 700; }
.contract-example pre { margin: 8px 0 0; padding: 14px; border-radius: 7px; overflow: auto; color: #d7e1e7; background: #1c2933; font-size: 11px; line-height: 1.7; }

@media (max-width: 950px) {
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 650px) {
  .metric-grid { grid-template-columns: 1fr; }
  .page-header__filters :deep(.el-input), .page-header__filters :deep(.el-select) { width: 100% !important; }
}
</style>
