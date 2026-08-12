<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { platformCapabilityGroups } from '../data/platformCapabilities'

const router = useRouter()
const keyword = ref('')
const phaseFilter = ref('全部版本')
const statusFilter = ref('全部状态')

const allItems = computed(() => platformCapabilityGroups.flatMap((group) => group.items))
const filteredGroups = computed(() => {
  const key = keyword.value.trim().toLowerCase()
  return platformCapabilityGroups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => {
        const matchesKey = !key || `${item.code}${item.name}${item.description}`.toLowerCase().includes(key)
        const matchesPhase = phaseFilter.value === '全部版本' || item.phase === phaseFilter.value
        const matchesStatus = statusFilter.value === '全部状态' || item.status === statusFilter.value
        return matchesKey && matchesPhase && matchesStatus
      }),
    }))
    .filter((group) => group.items.length)
})

const coverage = computed(() => ({
  total: allItems.value.length,
  ready: allItems.value.filter((item) => item.status === '已具备').length,
  active: allItems.value.filter((item) => item.status === '进行中').length,
  pending: allItems.value.filter((item) => ['待实施', '未启用'].includes(item.status)).length,
}))

const phaseStats = computed(() => ['M0', 'V0.1', 'V0.5', 'V1.0'].map((phase) => ({
  phase,
  count: allItems.value.filter((item) => item.phase === phase).length,
  ready: allItems.value.filter((item) => item.phase === phase && item.status === '已具备').length,
})))

function openCapability(item) {
  router.push(item.route)
}
</script>

<template>
  <div class="page-shell capability-page">
    <PageHeader
      eyebrow="PLATFORM CAPABILITY MAP"
      title="平台统一能力"
      description="平台只提供可复用机制和统一治理，不承载任何具体应用的领域字段、规则、状态或最终结果。"
    >
      <el-input v-model="keyword" clearable prefix-icon="Search" placeholder="搜索能力名称、编号或说明" style="width: 320px" />
      <el-select v-model="phaseFilter" style="width: 130px">
        <el-option v-for="item in ['全部版本', 'M0', 'V0.1', 'V0.5', 'V1.0', 'V1.1', 'V1.2', '后置']" :key="item" :label="item" :value="item" />
      </el-select>
      <el-select v-model="statusFilter" style="width: 130px">
        <el-option v-for="item in ['全部状态', '已具备', '进行中', '待实施', '未启用']" :key="item" :label="item" :value="item" />
      </el-select>
      <template #actions>
        <el-button><el-icon><Download /></el-icon>导出能力清单</el-button>
        <el-button type="primary"><el-icon><Plus /></el-icon>登记能力需求</el-button>
      </template>
    </PageHeader>

    <section class="capability-hero page-section">
      <div class="capability-hero__summary">
        <span class="summary-icon"><el-icon><Grid /></el-icon></span>
        <div>
          <small>统一平台基线</small>
          <strong>{{ coverage.total }} 项公共能力</strong>
          <p>覆盖部署、身份权限、应用接入、通知审计、运行治理和开发者体验。</p>
        </div>
      </div>
      <div class="coverage-numbers">
        <span><strong>{{ coverage.ready }}</strong><small>已具备</small></span>
        <span><strong>{{ coverage.active }}</strong><small>进行中</small></span>
        <span><strong>{{ coverage.pending }}</strong><small>待实施</small></span>
        <span class="coverage-rate"><strong>{{ Math.round(((coverage.ready + coverage.active) / coverage.total) * 100) }}%</strong><small>已启动</small></span>
      </div>
      <div class="coverage-bar">
        <i class="ready" :style="{ width: `${(coverage.ready / coverage.total) * 100}%` }" />
        <i class="active" :style="{ width: `${(coverage.active / coverage.total) * 100}%` }" />
        <i class="pending" :style="{ width: `${(coverage.pending / coverage.total) * 100}%` }" />
      </div>
    </section>

    <div class="capability-groups">
      <section v-for="group in filteredGroups" :key="group.key" class="capability-group surface-panel">
        <header>
          <span class="group-icon" :style="{ '--group-color': group.color }"><el-icon><component :is="group.icon" /></el-icon></span>
          <div><h2>{{ group.name }}</h2><p>{{ group.description }}</p></div>
          <em>{{ group.items.length }} 项</em>
        </header>
        <div class="capability-list">
          <button v-for="item in group.items" :key="item.code" type="button" @click="openCapability(item)">
            <span class="capability-code mono">{{ item.code }}</span>
            <div><strong>{{ item.name }}</strong><small>{{ item.description }}</small></div>
            <span class="capability-phase">{{ item.phase }}</span>
            <StatusTag :status="item.status" />
            <el-icon><ArrowRight /></el-icon>
          </button>
        </div>
      </section>
    </div>

    <section class="delivery-map page-section surface-panel">
      <div class="delivery-map__intro"><span>实施节奏</span><h2>先形成平台接入骨架，再逐步增强公共能力</h2><p>版本状态来自实施计划，不以真实业务应用上线作为平台退出条件。</p></div>
      <div class="delivery-phases">
        <article v-for="(phase, index) in phaseStats" :key="phase.phase">
          <span>{{ String(index + 1).padStart(2, '0') }}</span>
          <strong>{{ phase.phase }}</strong>
          <small>{{ phase.count }} 项能力 · {{ phase.ready }} 项已具备</small>
          <div><i :style="{ width: `${phase.count ? (phase.ready / phase.count) * 100 : 0}%` }" /></div>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.capability-hero { position: relative; display: grid; grid-template-columns: minmax(320px, 1fr) auto; align-items: center; gap: 28px; padding: 18px 20px 22px; border: 1px solid #d8e1e5; border-radius: 9px; overflow: hidden; background: #fff; }
.capability-hero__summary { display: flex; align-items: center; gap: 13px; }
.summary-icon { display: grid; flex: 0 0 48px; width: 48px; height: 48px; border-radius: 10px; color: #fff; background: var(--brand-800); font-size: 22px; place-items: center; }
.capability-hero__summary div { display: grid; gap: 3px; }
.capability-hero__summary small { color: var(--accent-600); font-size: 11px; font-weight: 750; letter-spacing: 0.1em; }
.capability-hero__summary strong { color: var(--ink-900); font-size: 20px; letter-spacing: -0.02em; }
.capability-hero__summary p { margin: 1px 0 0; color: var(--ink-500); font-size: 11px; }
.coverage-numbers { display: flex; align-items: center; }
.coverage-numbers > span { display: grid; min-width: 80px; gap: 3px; padding: 0 14px; border-left: 1px solid #e5eaed; text-align: center; }
.coverage-numbers > span:first-child { border-left: 0; }
.coverage-numbers strong { color: var(--ink-900); font-size: 20px; }
.coverage-numbers small { color: var(--ink-500); font-size: 10px; }
.coverage-numbers .coverage-rate strong { color: var(--accent-600); }
.coverage-bar { position: absolute; right: 0; bottom: 0; left: 0; display: flex; height: 4px; background: #e8ecee; }
.coverage-bar i { height: 100%; }
.coverage-bar .ready { background: #428368; }
.coverage-bar .active { background: #c37a32; }
.coverage-bar .pending { background: #a6b0b6; }
.capability-groups { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin-top: 14px; }
.capability-group { min-width: 0; overflow: hidden; }
.capability-group > header { display: grid; grid-template-columns: 42px minmax(0, 1fr) auto; align-items: center; gap: 11px; padding: 15px 16px; border-bottom: 1px solid #e7ebed; background: #fafbfb; }
.group-icon { display: grid; width: 40px; height: 40px; border-radius: 8px; color: var(--group-color); background: color-mix(in srgb, var(--group-color) 10%, white); font-size: 19px; place-items: center; }
.capability-group h2 { margin: 0; color: var(--ink-900); font-size: 15px; }
.capability-group header p { margin: 4px 0 0; color: var(--ink-500); font-size: 11px; }
.capability-group header em { color: var(--ink-500); font-size: 11px; font-style: normal; }
.capability-list { display: grid; }
.capability-list button { display: grid; grid-template-columns: 100px minmax(0, 1fr) 52px auto 14px; align-items: center; gap: 9px; min-height: 68px; padding: 10px 14px; border: 0; border-top: 1px solid #edf0f2; color: var(--ink-500); background: #fff; text-align: left; cursor: pointer; }
.capability-list button:first-child { border-top: 0; }
.capability-list button:hover { background: #f8f9fa; }
.capability-code { color: #74848d; font-size: 10px; }
.capability-list button > div { display: grid; min-width: 0; gap: 3px; }
.capability-list strong { color: var(--ink-900); font-size: 13px; }
.capability-list small { overflow: hidden; color: var(--ink-500); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.capability-phase { color: #647681; font-size: 11px; font-weight: 650; text-align: center; }
.delivery-map { display: grid; grid-template-columns: minmax(260px, 0.7fr) 1.3fr; overflow: hidden; }
.delivery-map__intro { padding: 20px; border-right: 1px solid #e5eaed; background: #f8fafb; }
.delivery-map__intro > span { color: var(--accent-600); font-size: 11px; font-weight: 750; letter-spacing: 0.1em; }
.delivery-map__intro h2 { margin: 7px 0 0; color: var(--ink-900); font-size: 16px; }
.delivery-map__intro p { margin: 7px 0 0; color: var(--ink-500); font-size: 11px; line-height: 1.6; }
.delivery-phases { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; background: #e7ebed; }
.delivery-phases article { display: grid; align-content: center; gap: 5px; min-height: 124px; padding: 14px; background: #fff; }
.delivery-phases article > span { color: #a3adb3; font-size: 10px; }
.delivery-phases strong { color: var(--ink-900); font-size: 14px; }
.delivery-phases small { color: var(--ink-500); font-size: 10px; }
.delivery-phases article div { height: 4px; margin-top: 5px; border-radius: 2px; overflow: hidden; background: #e9edef; }
.delivery-phases article i { display: block; height: 100%; border-radius: inherit; background: #4a7e68; }
@media (max-width: 1100px) { .capability-groups { grid-template-columns: 1fr; } .capability-hero { grid-template-columns: 1fr; } .coverage-numbers { justify-content: flex-start; } }
@media (max-width: 760px) { .delivery-map { grid-template-columns: 1fr; } .page-header__filters :deep(.el-input), .page-header__filters :deep(.el-select) { width: 100% !important; } .capability-list button { grid-template-columns: minmax(0, 1fr) 52px auto 14px; } .capability-code { display: none; } .delivery-map__intro { border-right: 0; border-bottom: 1px solid #e5eaed; } .delivery-phases { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 520px) { .coverage-numbers { display: grid; grid-template-columns: repeat(2, 1fr); width: 100%; } .coverage-numbers > span:nth-child(3) { border-left: 0; } .capability-list button { grid-template-columns: minmax(0, 1fr) auto 14px; } .capability-phase { display: none; } }
</style>
