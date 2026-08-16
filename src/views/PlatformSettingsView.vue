<script setup>
import { computed, onMounted, ref } from 'vue'
import ApiState from '../components/ApiState.vue'
import MetricCard from '../components/MetricCard.vue'
import PageHeader from '../components/PageHeader.vue'
import { apiRequest } from '../services/platformApi'

const activeTab = ref('targets')
const loading = ref(false)
const error = ref(null)
const targets = ref(null)

const tabs = [
  ['targets', '运行目标'],
  ['retention', '数据保留'],
  ['routes', '责任路由'],
  ['ha', '高可用触发'],
]

const dayLabels = {
  MON: '周一',
  TUE: '周二',
  WED: '周三',
  THU: '周四',
  FRI: '周五',
  SAT: '周六',
  SUN: '周日',
}

const serviceWindowText = computed(() => {
  if (!targets.value) return '—'
  const days = targets.value.service_window.days.map((day) => dayLabels[day] || day).join('、')
  return `${days} ${targets.value.service_window.start}–${targets.value.service_window.end}`
})

const sloRows = computed(() => targets.value ? [
  { name: '月度可用性', value: targets.value.slo.monthly_availability_percent, unit: '%', purpose: '生产服务可用性基线' },
  { name: '公开 API P95', value: targets.value.slo.public_api_p95_ms, unit: 'ms', purpose: '公开 API 延迟目标' },
  { name: '公开 API P99', value: targets.value.slo.public_api_p99_ms, unit: 'ms', purpose: '公开 API 尾延迟目标' },
  { name: '最低压测速率', value: targets.value.slo.minimum_test_rps, unit: 'RPS', purpose: '发布前最低负载规模' },
  { name: '最低压测请求数', value: targets.value.slo.minimum_test_requests, unit: '次', purpose: '发布前最低样本量' },
  { name: '最大服务端错误率', value: targets.value.slo.maximum_server_error_percent, unit: '%', purpose: '压测失败门槛' },
] : [])

const retentionRows = computed(() => targets.value ? [
  { name: '平台审计', value: targets.value.retention.audit_days, unit: '天', owner: '安全与审计' },
  { name: '站内测试通知', value: targets.value.retention.notification_days, unit: '天', owner: '平台运行' },
  { name: '到期门户会话', value: targets.value.retention.portal_session_days_after_expiry, unit: '天', owner: '身份与安全' },
  { name: '到期接入认证', value: targets.value.retention.conformance_days_after_expiry, unit: '天', owner: '接入治理' },
  { name: '小时备份保留', value: targets.value.retention.backup_hourly_count, unit: '份', owner: '数据恢复' },
  { name: '每日备份保留', value: targets.value.retention.backup_daily_days, unit: '天', owner: '数据恢复' },
] : [])

async function loadTargets() {
  loading.value = true
  error.value = null
  try {
    targets.value = await apiRequest('operations/targets')
  } catch (caught) {
    error.value = caught
  } finally {
    loading.value = false
  }
}

onMounted(loadTargets)
</script>

<template>
  <div class="page-shell settings-page">
    <PageHeader
      title="平台配置"
      description="只读展示受版本控制的生产运行目标；变更必须修改配置文件并通过代码评审与发布门禁。"
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
        <el-button type="primary" :loading="loading" @click="loadTargets">
          <el-icon><Refresh /></el-icon>刷新配置
        </el-button>
      </template>
    </PageHeader>

    <ApiState :loading="loading" :error="error" :empty="!targets" empty-text="未加载到生产配置" @retry="loadTargets">
      <template v-if="targets">
        <div class="metric-grid page-section">
          <MetricCard label="部署层级" :value="targets.deployment.tier === 'STANDARD_SINGLE_NODE' ? '单机标准档' : targets.deployment.tier" :hint="`${targets.deployment.tier} · ${targets.deployment.profile}`" icon="SetUp" tone="blue" />
          <MetricCard label="月度可用性" :value="targets.slo.monthly_availability_percent" unit="%" hint="生产 SLO" icon="DataLine" tone="green" />
          <MetricCard label="恢复点目标" :value="targets.recovery.rpo_minutes" unit="分钟" hint="RPO" icon="Clock" tone="blue" />
          <MetricCard label="恢复时间目标" :value="targets.recovery.rto_minutes" unit="分钟" hint="RTO" icon="RefreshLeft" tone="green" />
        </div>

        <section class="surface-panel configuration-identity page-section">
          <div class="section-heading">
            <div>
              <h2>配置身份</h2>
              <p>此页面没有写接口，运行参数以仓库中的受控配置文件为唯一来源。</p>
            </div>
            <el-tag type="success" effect="plain">只读 · 配置即代码</el-tag>
          </div>
          <el-descriptions :column="4" border>
            <el-descriptions-item label="配置模式">{{ targets.configuration_mode }}</el-descriptions-item>
            <el-descriptions-item label="Schema 版本">v{{ targets.schema_version }}</el-descriptions-item>
            <el-descriptions-item label="时区">{{ targets.timezone }}</el-descriptions-item>
            <el-descriptions-item label="可在线编辑">{{ targets.editable ? '是' : '否' }}</el-descriptions-item>
            <el-descriptions-item label="配置来源" :span="4"><code>{{ targets.source }}</code></el-descriptions-item>
          </el-descriptions>
        </section>

        <template v-if="activeTab === 'targets'">
          <div class="target-grid page-section">
            <section class="surface-panel detail-panel">
              <div class="section-heading"><div><h2>部署与服务窗口</h2><p>当前生产部署档位及计划维护约束。</p></div></div>
              <el-descriptions :column="1" border>
                <el-descriptions-item label="部署拓扑"><code>{{ targets.deployment.topology }}</code></el-descriptions-item>
                <el-descriptions-item label="部署 Profile"><code>{{ targets.deployment.profile }}</code></el-descriptions-item>
                <el-descriptions-item label="异机备份">{{ targets.deployment.off_host_backup_required ? '必须' : '非必须' }}</el-descriptions-item>
                <el-descriptions-item label="服务窗口">{{ serviceWindowText }}</el-descriptions-item>
                <el-descriptions-item label="维护提前通知">{{ targets.service_window.planned_maintenance_notice_hours }} 小时</el-descriptions-item>
              </el-descriptions>
            </section>
            <section class="surface-panel detail-panel">
              <div class="section-heading"><div><h2>恢复目标</h2><p>备份频率与平台恢复门禁。</p></div></div>
              <el-descriptions :column="1" border>
                <el-descriptions-item label="备份间隔">{{ targets.recovery.backup_interval_minutes }} 分钟</el-descriptions-item>
                <el-descriptions-item label="平台 RPO">{{ targets.recovery.rpo_minutes }} 分钟</el-descriptions-item>
                <el-descriptions-item label="平台 RTO">{{ targets.recovery.rto_minutes }} 分钟</el-descriptions-item>
              </el-descriptions>
            </section>
          </div>
          <section class="surface-panel table-panel page-section">
            <div class="section-heading"><div><h2>服务水平与容量门禁</h2><p>发布与运行诊断共同使用这些目标值。</p></div></div>
            <el-table :data="sloRows" style="width: 100%">
              <el-table-column prop="name" label="目标" min-width="220" />
              <el-table-column prop="value" label="当前值" min-width="140" />
              <el-table-column prop="unit" label="单位" width="110" />
              <el-table-column prop="purpose" label="用途" min-width="300" />
            </el-table>
          </section>
        </template>

        <section v-else-if="activeTab === 'retention'" class="surface-panel table-panel page-section">
          <div class="section-heading"><div><h2>数据保留策略</h2><p>只定义平台数据生命周期，不定义独立应用业务数据生命周期。</p></div></div>
          <el-table :data="retentionRows" style="width: 100%">
            <el-table-column prop="name" label="平台数据" min-width="260" />
            <el-table-column prop="value" label="保留值" min-width="140" />
            <el-table-column prop="unit" label="单位" width="110" />
            <el-table-column prop="owner" label="责任域" min-width="220" />
          </el-table>
        </section>

        <section v-else-if="activeTab === 'routes'" class="surface-panel table-panel page-section">
          <div class="section-heading"><div><h2>告警责任路由</h2><p>责任角色是运行手册中的升级路径，不是外部消息供应商配置。</p></div></div>
          <el-table :data="targets.alert_routes" style="width: 100%">
            <el-table-column prop="route_key" label="告警域" min-width="260"><template #default="scope"><code>{{ scope.row.route_key }}</code></template></el-table-column>
            <el-table-column prop="primary" label="第一责任角色" min-width="220"><template #default="scope"><code>{{ scope.row.primary }}</code></template></el-table-column>
            <el-table-column prop="backup" label="备份责任角色" min-width="220"><template #default="scope"><code>{{ scope.row.backup }}</code></template></el-table-column>
            <el-table-column prop="acknowledge_minutes" label="确认时限" width="130"><template #default="scope">{{ scope.row.acknowledge_minutes }} 分钟</template></el-table-column>
          </el-table>
        </section>

        <section v-else class="surface-panel ha-panel page-section">
          <div class="section-heading"><div><h2>高可用升级触发条件</h2><p>任一条件成为持续业务要求时，应从单机标准档升级高可用拓扑。</p></div></div>
          <el-descriptions :column="2" border>
            <el-descriptions-item label="可用性要求">≥ {{ targets.ha_upgrade_triggers.availability_percent }}%</el-descriptions-item>
            <el-descriptions-item label="持续吞吐要求">≥ {{ targets.ha_upgrade_triggers.sustained_rps }} RPS</el-descriptions-item>
            <el-descriptions-item label="RPO 要求">≤ {{ targets.ha_upgrade_triggers.rpo_minutes }} 分钟</el-descriptions-item>
            <el-descriptions-item label="RTO 要求">≤ {{ targets.ha_upgrade_triggers.rto_minutes }} 分钟</el-descriptions-item>
          </el-descriptions>
        </section>
      </template>
    </ApiState>
  </div>
</template>

<style scoped>
.management-tabs { display: flex; gap: 24px; }
.management-tabs button { position: relative; height: 46px; padding: 0; border: 0; color: var(--ink-500); background: transparent; font-size: 15px; cursor: pointer; }
.management-tabs button.active { color: var(--ink-900); font-weight: 650; }
.management-tabs button.active::after { position: absolute; right: 0; bottom: 0; left: 0; height: 2px; background: var(--accent-500); content: ''; }
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.configuration-identity, .detail-panel, .table-panel, .ha-panel { padding: 18px; overflow: hidden; }
.configuration-identity .section-heading, .detail-panel .section-heading, .table-panel .section-heading, .ha-panel .section-heading { align-items: center; }
.target-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
code { color: #416c83; word-break: break-all; }
@media (max-width: 1000px) { .metric-grid, .target-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .configuration-identity :deep(.el-descriptions__body) { overflow-x: auto; } }
@media (max-width: 650px) { .metric-grid, .target-grid { grid-template-columns: 1fr; } .management-tabs { gap: 16px; overflow-x: auto; } }
</style>
