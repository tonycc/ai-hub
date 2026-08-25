<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import ApiState from '../components/ApiState.vue'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { apiRequest } from '../services/platformApi'
import { usePortalSession } from '../stores/session'

const router = useRouter()
const session = usePortalSession()
const registeredApps = ref([])
const myApps = ref([])
const applicationsLoading = ref(false)
const applicationsError = ref(null)
const myAppsLoading = ref(false)
const myAppsError = ref(null)
const operationsSummary = ref(null)
const operationsError = ref(null)
const operationsLoading = ref(false)
const canReadOperations = computed(() => session.hasPermission('platform.operations.read'))
const isPlatformUser = computed(() => session.hasPermission('platform.application.read'))

async function loadApplications() {
  if (!isPlatformUser.value) return
  applicationsLoading.value = true
  applicationsError.value = null
  try {
    registeredApps.value = (await apiRequest('applications')).items
  } catch (error) {
    applicationsError.value = error
  } finally {
    applicationsLoading.value = false
  }
}

async function loadMyApplications() {
  myAppsLoading.value = true
  myAppsError.value = null
  try {
    myApps.value = (await apiRequest('my-applications')).items
  } catch (error) {
    myAppsError.value = error
  } finally {
    myAppsLoading.value = false
  }
}

async function loadOperationsSummary() {
  if (!canReadOperations.value) return
  operationsLoading.value = true
  operationsError.value = null
  try {
    operationsSummary.value = await apiRequest('operations/summary')
  } catch (error) {
    operationsSummary.value = null
    operationsError.value = error
  } finally {
    operationsLoading.value = false
  }
}

const activeApps = computed(() => registeredApps.value.filter((app) => app.status === 'ACTIVE'))
const healthyEntries = computed(() => (operationsSummary.value?.application_entries || [])
  .filter((entry) => entry.status === 'HEALTHY').length)
const totalEntries = computed(() => (operationsSummary.value?.application_entries || []).length)
const operationsState = computed(() => {
  if (operationsLoading.value) return 'loading'
  if (operationsError.value) return 'error'
  if (!operationsSummary.value) return 'unavailable'
  return operationsSummary.value.overall_status === 'HEALTHY' ? 'healthy' : 'degraded'
})
const operationsLabel = computed(() => ({
  loading: '加载中',
  error: '不可用',
  unavailable: '暂无数据',
  healthy: '正常',
  degraded: '降级',
}[operationsState.value]))
const operationsHint = computed(() => {
  if (operationsState.value === 'loading') return '正在获取运行状态'
  if (operationsState.value === 'error') return operationsError.value?.message || '运维接口不可用'
  if (operationsState.value === 'unavailable') return '尚未获取到运行状态'
  return `观测于 ${formatTime(operationsSummary.value?.observed_at)}`
})
const operationsTone = computed(() => ({
  loading: 'blue',
  error: 'orange',
  unavailable: 'blue',
  healthy: 'blue',
  degraded: 'orange',
}[operationsState.value]))

const platformEntries = [
  { name: '应用中心', description: '登记环境、入口、回调、能力和版本', path: '/applications', icon: 'Grid', color: '#416f86', permission: 'platform.application.read' },
  { name: '用户与组织', description: '管理身份映射、组织和账号状态', path: '/platform/identity', icon: 'UserFilled', color: '#527a64', permission: 'platform.identity.read' },
  { name: '权限与安全', description: '管理角色、权限点、权限范围和数据范围', path: '/platform/permissions', icon: 'Lock', color: '#735f84', permission: 'platform.authorization.read' },
  { name: '开发者中心', description: '查看契约、SDK、沙箱和认证结果', path: '/platform/developer', icon: 'Tools', color: '#826846', permission: 'platform.developer.read' },
  { name: '运维中心', description: '应用入口、数据来源和同步新鲜度诊断', path: '/platform/operations', icon: 'Monitor', color: '#4a7a8c', permission: 'platform.operations.read' },
  { name: '平台配置', description: '只读生产目标、保留策略与责任路由', path: '/platform/settings', icon: 'Setting', color: '#6b7a8c', permission: 'platform.operations.read' },
]
const visiblePlatformEntries = computed(() => platformEntries.filter((entry) => session.hasPermission(entry.permission)))

function formatTime(value) {
  if (!value) return '从未检查'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

onMounted(() => {
  loadApplications()
  loadMyApplications()
  loadOperationsSummary()
})
</script>

<template>
  <div class="page-shell portal-page">
    <PageHeader
      eyebrow="AI HUB PLATFORM"
      title="平台控制台"
      description="管理应用接入、身份权限、通知审计和平台运行状态。"
    />

    <div class="metric-grid page-section">
      <template v-if="isPlatformUser">
        <MetricCard label="已登记应用" :value="registeredApps.length" unit="个" hint="当前权限范围内" icon="Grid" tone="blue" />
        <MetricCard label="运行中应用" :value="activeApps.length" unit="个" hint="状态为 ACTIVE" icon="CircleCheck" tone="green" />
      </template>
      <template v-else>
        <MetricCard label="我的应用" :value="myApps.length" unit="个" hint="有权限访问的应用" icon="Grid" tone="green" />
      </template>
      <MetricCard v-if="canReadOperations" label="健康应用入口" :value="operationsSummary ? `${healthyEntries}/${totalEntries}` : '—'" hint="最近一次健康检查" icon="Monitor" :tone="operationsState === 'healthy' ? 'green' : 'orange'" />
      <MetricCard v-if="canReadOperations" label="整体状态" :value="operationsLabel" :hint="operationsHint" icon="Flag" :tone="operationsTone" />
    </div>

    <section v-if="canReadOperations && operationsState === 'error'" class="attention-strip" aria-label="运行状态提醒">
      <span class="attention-strip__mark"><el-icon><Warning /></el-icon></span>
      <div>
        <strong>运行状态不可用</strong>
        <small>{{ operationsError?.message || '运维接口暂时不可用，请稍后重试。' }}</small>
      </div>
      <el-button text type="primary" @click="loadOperationsSummary">重试<el-icon><ArrowRight /></el-icon></el-button>
    </section>

    <section v-if="operationsState === 'degraded'" class="attention-strip" aria-label="运行状态提醒">
      <span class="attention-strip__mark"><el-icon><Warning /></el-icon></span>
      <div>
        <strong>存在需要关注的应用入口</strong>
        <small>部分应用入口健康检查未通过或长时间未上报，请前往运维中心查看对象级诊断。</small>
      </div>
      <el-button text type="primary" @click="router.push('/platform/operations')">查看运维中心<el-icon><ArrowRight /></el-icon></el-button>
    </section>

    <section v-if="!isPlatformUser" class="page-section">
      <div class="section-heading">
        <div><h2>我的应用</h2><p>您有权限访问的业务系统</p></div>
      </div>
      <div class="application-grid">
        <ApiState :loading="myAppsLoading" :error="myAppsError" :empty="!myApps.length" empty-text="您暂无应用访问权限" @retry="loadMyApplications">
        <a v-for="app in myApps" :key="app.application_id" class="surface-panel application-card" :class="{ 'application-card--disabled': !app.portal_url }" :href="app.portal_url || undefined" :target="app.portal_url ? '_blank' : undefined" :rel="app.portal_url ? 'noopener noreferrer' : undefined" :aria-disabled="!app.portal_url">
          <span style="--app-color: #527a64"><el-icon><Connection /></el-icon></span>
          <div><strong>{{ app.name }}</strong><small>{{ app.description }}</small></div>
          <div class="application-card__status">
            <small>{{ app.portal_url ? '状态' : '暂无可用入口' }}</small>
            <StatusTag :status="app.status" size="default" />
          </div>
        </a>
        </ApiState>
      </div>
    </section>

    <section v-if="isPlatformUser" class="page-section">
      <div class="section-heading">
        <div><h2>已登记应用</h2><p>点击应用查看详情、环境和密钥</p></div>
        <el-button text @click="router.push('/applications')">应用中心<el-icon><ArrowRight /></el-icon></el-button>
      </div>
      <div class="application-grid">
        <ApiState :loading="applicationsLoading" :error="applicationsError" :empty="!registeredApps.length" empty-text="当前权限范围内暂无应用" @retry="loadApplications">
        <RouterLink v-for="app in registeredApps" :key="app.application_id" class="surface-panel application-card" :to="{ path: '/applications', query: { app: app.application_id } }">
          <span style="--app-color: #416f86"><el-icon><Connection /></el-icon></span>
          <div><strong>{{ app.name }}</strong><small>{{ app.description }}</small><code>{{ app.application_id }}</code></div>
          <div class="application-card__status"><small>状态</small><StatusTag :status="app.status" size="default" /></div>
        </RouterLink>
        </ApiState>
      </div>
    </section>

    <section class="page-section">
      <div class="section-heading">
        <div><h2>管理入口</h2><p>按当前角色权限显示</p></div>
      </div>
      <div class="platform-service-grid">
        <button v-for="entry in visiblePlatformEntries" :key="entry.path" type="button" @click="router.push(entry.path)">
          <span :style="{ '--entry-color': entry.color }"><el-icon><component :is="entry.icon" /></el-icon></span>
          <div><strong>{{ entry.name }}</strong><small>{{ entry.description }}</small></div>
          <el-icon><ArrowRight /></el-icon>
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.attention-strip { display: grid; grid-template-columns: 38px minmax(0, 1fr) auto; align-items: center; gap: var(--space-gap); margin-top: var(--space-gap); padding: var(--space-card-lg); border: 1px solid #e6d7c8; border-left: 3px solid #d97706; border-radius: 7px; background: #fffaf6; }
.attention-strip__mark { display: grid; width: 36px; height: 36px; border-radius: 7px; color: #b45309; background: #fef3c7; place-items: center; }
.attention-strip > div { display: grid; gap: 4px; }
.attention-strip strong { color: #57382b; font-size: var(--font-body-lg); }
.attention-strip small { color: #876d61; font-size: var(--font-caption); }
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--space-gap-lg); }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: var(--space-gap); margin-bottom: var(--space-gap); }
.section-heading h2 { margin: 0; color: var(--ink-900); font-size: var(--font-heading); font-weight: 600; }
.section-heading p { margin: 4px 0 0; color: var(--ink-500); font-size: var(--font-body); }
.platform-service-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-gap-lg); }
.platform-service-grid button { display: grid; grid-template-columns: 46px minmax(0, 1fr) auto; align-items: center; gap: var(--space-gap); min-height: 92px; padding: var(--space-card); border: 1px solid var(--line); border-radius: 8px; color: var(--ink-500); background: #fff; text-align: left; cursor: pointer; }
.platform-service-grid button:hover { border-color: #b9c8cf; box-shadow: var(--shadow-sm); }
.platform-service-grid button > span { display: grid; width: 44px; height: 44px; border-radius: 8px; color: var(--entry-color); background: color-mix(in srgb, var(--entry-color) 10%, white); font-size: 20px; place-items: center; }
.platform-service-grid button > div { display: grid; min-width: 0; gap: 5px; }
.platform-service-grid strong { color: var(--ink-900); font-size: var(--font-body-lg); }
.platform-service-grid small { color: var(--ink-500); font-size: var(--font-caption); line-height: 1.5; }
.application-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-gap-lg); }
.application-grid > :deep(.el-result), .application-grid > :deep(.el-empty), .application-grid > :deep(.api-state) { grid-column: 1 / -1; }
.application-card { display: grid; grid-template-columns: 48px minmax(0, 1fr) auto; align-items: center; gap: var(--space-gap); padding: var(--space-card-lg); cursor: pointer; color: inherit; text-decoration: none; }
.application-card:hover { border-color: #b9c8cf; box-shadow: var(--shadow-sm); }
.application-card--disabled { cursor: not-allowed; opacity: 0.72; }
.application-card--disabled:hover { border-color: inherit; box-shadow: none; }
.application-card > span { display: grid; width: 46px; height: 46px; border-radius: 9px; color: var(--app-color); background: color-mix(in srgb, var(--app-color) 10%, white); font-size: 21px; place-items: center; }
.application-card > div { display: grid; min-width: 0; gap: 5px; }
.application-card strong { color: var(--ink-900); font-size: var(--font-body-lg); }
.application-card small { color: var(--ink-500); font-size: var(--font-caption); line-height: 1.5; }
.application-card code { color: #71818b; font-size: var(--font-eyebrow); }
.application-card__status { display: grid; justify-items: end; gap: 4px; }
.application-card__status small { color: var(--ink-400); font-size: var(--font-eyebrow); }
@media (max-width: 1100px) { .metric-grid, .platform-service-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 700px) { .attention-strip { grid-template-columns: 38px 1fr; } .attention-strip .el-button { grid-column: 2; justify-self: start; } .metric-grid, .platform-service-grid, .application-grid { grid-template-columns: 1fr; } }
</style>
