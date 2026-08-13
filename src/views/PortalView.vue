<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import ApiState from '../components/ApiState.vue'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { apiRequest } from '../services/platformApi'
import { usePortalSession } from '../stores/session'

const router = useRouter()
const session = usePortalSession()
const registeredApps = ref([])
const applicationsLoading = ref(false)
const applicationsError = ref(null)
const canCreateApplication = computed(() => session.hasPermission('platform.application.write')
  && session.principal.value?.application_scopes?.['platform.application.write'] === null)

async function loadApplications() {
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

const milestoneTasks = [
  { code: 'M3-01', name: '管理端信息架构、角色任务与验收基线', owner: '平台产品', status: '已完成' },
  { code: 'M3-02', name: '用户、组织、平台角色与数据范围治理', owner: '平台研发', status: '已完成' },
  { code: 'M3-03', name: '应用、环境、scope、凭据与版本生命周期', owner: '平台研发', status: '已完成' },
  { code: 'M3-04', name: '通知配置、测试送达与追加式审计', owner: '平台研发', status: '已完成' },
  { code: 'M3-05', name: '公开契约、SDK、沙箱与开发者中心', owner: '接入工具', status: '已完成' },
  { code: 'M3-06', name: '接入认证、运行证据与只读运维诊断', owner: '平台运维', status: '已完成' },
  { code: 'M3-07', name: '四类平台角色真实环境 UAT', owner: '平台验收', status: '已完成' },
]

const platformEntries = [
  { name: '应用中心', description: '登记环境、入口、回调、能力和版本', path: '/applications', icon: 'Grid', color: '#416f86', permission: 'platform.application.read' },
  { name: '用户与组织', description: '管理身份映射、组织和账号状态', path: '/platform/identity', icon: 'UserFilled', color: '#527a64', permission: 'platform.identity.read' },
  { name: '权限与安全', description: '管理角色、权限点、scope 和数据范围', path: '/platform/permissions', icon: 'Lock', color: '#735f84', permission: 'platform.authorization.read' },
  { name: '开发者中心', description: '查看契约、SDK、沙箱和认证结果', path: '/platform/developer', icon: 'Tools', color: '#826846', permission: 'platform.developer.read' },
]
const visiblePlatformEntries = computed(() => platformEntries.filter((entry) => session.hasPermission(entry.permission)))
onMounted(loadApplications)
</script>

<template>
  <div class="page-shell portal-page">
    <PageHeader
      eyebrow="PLATFORM IMPLEMENTATION · M3"
      title="AI Hub 平台控制台"
      description="当前只建设平台公共能力。真实业务应用通过公开 API 和事件独立接入，不进入平台源码、数据库或发布制品。"
    >
      <template #actions>
        <el-button @click="router.push('/platform/developer')"><el-icon><Document /></el-icon>接入文档</el-button>
        <el-button v-if="canCreateApplication" type="primary" @click="router.push('/applications')"><el-icon><Plus /></el-icon>注册应用</el-button>
      </template>
    </PageHeader>

    <section class="attention-strip" aria-label="当前实施提醒">
      <span class="attention-strip__mark"><el-icon><CircleCheck /></el-icon></span>
      <div>
        <strong>M3 已完成：平台公共能力与四类角色 UAT 已通过</strong>
        <small>管理、接入、通知、审计、凭据、运行证据和运维诊断均已验证；下一阶段进入 M4 生产准备。</small>
      </div>
      <el-button text type="primary" @click="router.push('/platform/operations')">查看运行基线<el-icon><ArrowRight /></el-icon></el-button>
    </section>

    <div class="metric-grid page-section">
      <MetricCard label="当前里程碑" value="M3" hint="平台公共能力已验收" icon="Flag" tone="blue" />
      <MetricCard label="已登记应用" :value="registeredApps.length" unit="个" hint="均为中性认证配置" icon="Grid" tone="blue" />
      <MetricCard label="开发者资产" value="5" unit="份" hint="契约 · SDK · 文档 · 示例" icon="Document" tone="green" />
      <MetricCard label="M3 任务" value="7" unit="项" hint="角色 UAT 与门禁通过" icon="CircleCheck" tone="green" />
    </div>

    <div class="work-grid page-section">
      <section class="surface-panel milestone-panel">
        <header class="panel-header">
          <div><h2>M3 实施门禁</h2><p>平台公共能力、接入认证与角色边界均已验证</p></div>
          <el-button text @click="router.push('/platform')">能力路线<el-icon><ArrowRight /></el-icon></el-button>
        </header>
        <div class="milestone-list">
          <article v-for="task in milestoneTasks" :key="task.code">
            <code>{{ task.code }}</code>
            <div><strong>{{ task.name }}</strong><small>{{ task.owner }}</small></div>
            <StatusTag :status="task.status" />
          </article>
        </div>
      </section>

      <section class="surface-panel boundary-panel">
        <header class="panel-header"><div><h2>平台边界</h2><p>所有实现必须持续满足</p></div></header>
        <ul>
          <li><el-icon><CircleCheck /></el-icon><span>平台只提供通用机制与治理</span></li>
          <li><el-icon><CircleCheck /></el-icon><span>独立应用拥有自己的数据与规则</span></li>
          <li><el-icon><CircleCheck /></el-icon><span>跨项目只依赖版本化公开契约</span></li>
          <li><el-icon><CircleCheck /></el-icon><span>平台投影只读且可以从来源重建</span></li>
          <li><el-icon><CircleCheck /></el-icon><span>API-only 应用不强制安装事件组件</span></li>
        </ul>
      </section>
    </div>

    <section class="page-section">
      <div class="section-heading">
        <div><h2>平台治理入口</h2><p>面向平台管理员、应用开发者、安全和运维角色</p></div>
        <el-button text @click="router.push('/platform')">查看能力总览<el-icon><ArrowRight /></el-icon></el-button>
      </div>
      <div class="platform-service-grid">
        <button v-for="entry in visiblePlatformEntries" :key="entry.path" type="button" @click="router.push(entry.path)">
          <span :style="{ '--entry-color': entry.color }"><el-icon><component :is="entry.icon" /></el-icon></span>
          <div><strong>{{ entry.name }}</strong><small>{{ entry.description }}</small></div>
          <el-icon><ArrowRight /></el-icon>
        </button>
      </div>
    </section>

    <section class="page-section">
      <div class="section-heading">
        <div><h2>接入认证配置</h2><p>参考应用只是平台一致性测试夹具，不承载真实领域功能</p></div>
        <el-button text @click="router.push('/applications')">应用中心<el-icon><ArrowRight /></el-icon></el-button>
      </div>
      <div class="application-grid">
        <ApiState :loading="applicationsLoading" :error="applicationsError" :empty="!registeredApps.length" empty-text="当前权限范围内暂无应用" @retry="loadApplications">
        <article v-for="app in registeredApps" :key="app.application_id" class="surface-panel application-card">
          <span style="--app-color: #416f86"><el-icon><Connection /></el-icon></span>
          <div><strong>{{ app.name }}</strong><small>{{ app.description }}</small><code>{{ app.application_id }}</code></div>
          <StatusTag :status="app.status" />
        </article>
        </ApiState>
      </div>
    </section>
  </div>
</template>

<style scoped>
.attention-strip { display: grid; grid-template-columns: 38px minmax(0, 1fr) auto; align-items: center; gap: 12px; margin-top: 12px; padding: 12px 14px; border: 1px solid #e6d7c8; border-left: 3px solid var(--accent-500); border-radius: 7px; background: #fffaf6; }
.attention-strip__mark { display: grid; width: 34px; height: 34px; border-radius: 7px; color: var(--accent-600); background: var(--accent-100); place-items: center; }
.attention-strip > div { display: grid; gap: 3px; }
.attention-strip strong { color: #57382b; font-size: 13px; }
.attention-strip small { color: #876d61; font-size: 11px; }
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.work-grid { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(300px, 0.65fr); gap: 14px; }
.panel-header, .section-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.panel-header { padding: 14px 16px; border-bottom: 1px solid var(--line); }
.panel-header h2, .section-heading h2 { margin: 0; color: var(--ink-900); font-size: 14px; }
.panel-header p, .section-heading p { margin: 4px 0 0; color: var(--ink-500); font-size: 11px; }
.milestone-list { display: grid; }
.milestone-list article { display: grid; grid-template-columns: 74px minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 11px 16px; border-top: 1px solid #edf0f2; }
.milestone-list article:first-child { border-top: 0; }
.milestone-list code { color: #416c83; font-size: 11px; }
.milestone-list article > div { display: grid; gap: 3px; }
.milestone-list strong { color: var(--ink-900); font-size: 12px; }
.milestone-list small { color: var(--ink-500); font-size: 10px; }
.boundary-panel ul { display: grid; gap: 12px; margin: 0; padding: 18px; list-style: none; }
.boundary-panel li { display: flex; align-items: flex-start; gap: 8px; color: var(--ink-700); font-size: 12px; line-height: 1.5; }
.boundary-panel li .el-icon { flex: 0 0 auto; margin-top: 2px; color: #438167; }
.section-heading { margin-bottom: 10px; }
.platform-service-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.platform-service-grid button { display: grid; grid-template-columns: 42px minmax(0, 1fr) auto; align-items: center; gap: 10px; min-height: 84px; padding: 13px; border: 1px solid var(--line); border-radius: 8px; color: var(--ink-500); background: #fff; text-align: left; cursor: pointer; }
.platform-service-grid button:hover { border-color: #b9c8cf; box-shadow: var(--shadow-sm); }
.platform-service-grid button > span { display: grid; width: 40px; height: 40px; border-radius: 8px; color: var(--entry-color); background: color-mix(in srgb, var(--entry-color) 10%, white); font-size: 18px; place-items: center; }
.platform-service-grid button > div { display: grid; min-width: 0; gap: 4px; }
.platform-service-grid strong { color: var(--ink-900); font-size: 13px; }
.platform-service-grid small { color: var(--ink-500); font-size: 10px; line-height: 1.5; }
.application-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.application-grid > :deep(.el-result), .application-grid > :deep(.el-empty), .application-grid > :deep(.api-state) { grid-column: 1 / -1; }
.application-card { display: grid; grid-template-columns: 44px minmax(0, 1fr) auto; align-items: center; gap: 11px; padding: 15px; }
.application-card > span { display: grid; width: 42px; height: 42px; border-radius: 9px; color: var(--app-color); background: color-mix(in srgb, var(--app-color) 10%, white); font-size: 19px; place-items: center; }
.application-card > div { display: grid; min-width: 0; gap: 4px; }
.application-card strong { color: var(--ink-900); font-size: 13px; }
.application-card small { color: var(--ink-500); font-size: 10px; line-height: 1.5; }
.application-card code { color: #71818b; font-size: 10px; }
@media (max-width: 1100px) { .metric-grid, .platform-service-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .work-grid { grid-template-columns: 1fr; } }
@media (max-width: 700px) { .attention-strip { grid-template-columns: 38px 1fr; } .attention-strip .el-button { grid-column: 2; justify-self: start; } .metric-grid, .platform-service-grid, .application-grid { grid-template-columns: 1fr; } }
</style>
