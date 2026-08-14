<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import ApiState from '../components/ApiState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { apiRequest, queryString } from '../services/platformApi'
import { usePortalSession } from '../stores/session'

const session = usePortalSession()
const loading = ref(false)
const error = ref(null)
const applications = ref([])
const keyword = ref('')
const statusFilter = ref('')
const detailVisible = ref(false)
const detailLoading = ref(false)
const selected = ref(null)
const createVisible = ref(false)
const appEditVisible = ref(false)
const environmentVisible = ref(false)
const releaseVisible = ref(false)
const secretVisible = ref(false)
const oneTimeSecret = ref(null)
const saving = ref(false)
const scopeOptions = ref([])

const capabilityOptions = [
  ['API_CLIENT', 'API 客户端'],
  ['EVENT_PUBLISHER', '事件发布'],
  ['EVENT_CONSUMER', '事件消费'],
  ['PROJECTION_SOURCE', '投影来源'],
  ['PROJECTION_READER', '投影读取'],
]

const createForm = reactive({
  application_id: '', name: '', description: '', owner: '', capabilities: ['API_CLIENT'],
})
const appForm = reactive({ name: '', description: '', owner: '', status: 'DRAFT', capabilities: [] })
const environmentForm = reactive({
  environment: 'local', portal_url: '', api_base_url: '', health_url: '',
  redirect_text: '', version: '0.1.0', status: 'ACTIVE',
})
const releaseForm = reactive({ environment: '', version: '0.1.0', activate: true })

const canCreate = computed(() => session.hasPermission('platform.application.write')
  && session.principal.value?.application_scopes?.['platform.application.write'] === null)
const canEditSelected = computed(() => selected.value
  && session.hasPermission('platform.application.write', selected.value.application_id))
const canRotateSelected = computed(() => selected.value
  && session.hasPermission('platform.credential.rotate', selected.value.application_id))
const canRevokeSelected = computed(() => selected.value
  && session.hasPermission('platform.credential.revoke', selected.value.application_id))

function formatTime(value) {
  return value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—'
}

async function loadApplications() {
  loading.value = true
  error.value = null
  try {
    const response = await apiRequest(`applications${queryString({ query: keyword.value, status: statusFilter.value })}`)
    applications.value = response.items
  } catch (caught) {
    error.value = caught
  } finally {
    loading.value = false
  }
}

async function loadDetail(applicationId) {
  detailLoading.value = true
  try {
    selected.value = await apiRequest(`applications/${applicationId}`)
    detailVisible.value = true
  } catch (caught) {
    ElMessage.error(caught.message)
  } finally {
    detailLoading.value = false
  }
}

async function submitCreate() {
  saving.value = true
  try {
    const created = await apiRequest('applications', { method: 'POST', body: createForm })
    ElMessage.success('应用已注册')
    createVisible.value = false
    await loadApplications()
    await loadDetail(created.application_id)
  } catch (caught) {
    ElMessage.error(caught.message)
  } finally {
    saving.value = false
  }
}

function openEdit() {
  Object.assign(appForm, {
    name: selected.value.name,
    description: selected.value.description,
    owner: selected.value.owner,
    status: selected.value.status,
    capabilities: [...selected.value.capabilities],
  })
  appEditVisible.value = true
}

async function submitEdit() {
  saving.value = true
  try {
    selected.value = await apiRequest(`applications/${selected.value.application_id}`, {
      method: 'PUT', body: appForm,
    })
    appEditVisible.value = false
    ElMessage.success('应用配置已保存')
    await loadApplications()
  } catch (caught) {
    ElMessage.error(caught.message)
  } finally {
    saving.value = false
  }
}

function openEnvironment(environment = null) {
  Object.assign(environmentForm, environment ? {
    environment: environment.environment,
    portal_url: environment.portal_url,
    api_base_url: environment.api_base_url,
    health_url: environment.health_url,
    redirect_text: environment.oidc_redirect_uris.join('\n'),
    version: environment.version,
    status: environment.status,
  } : {
    environment: 'local', portal_url: '', api_base_url: '', health_url: '',
    redirect_text: '', version: '0.1.0', status: 'ACTIVE',
  })
  environmentVisible.value = true
}

async function submitEnvironment() {
  const redirectUris = environmentForm.redirect_text.split('\n').map((item) => item.trim()).filter(Boolean)
  saving.value = true
  try {
    selected.value = await apiRequest(
      `applications/${selected.value.application_id}/environments/${environmentForm.environment}`,
      {
        method: 'PUT',
        body: {
          portal_url: environmentForm.portal_url,
          api_base_url: environmentForm.api_base_url,
          health_url: environmentForm.health_url,
          oidc_redirect_uris: redirectUris,
          version: environmentForm.version,
          status: environmentForm.status,
        },
      },
    )
    environmentVisible.value = false
    ElMessage.success('环境配置已保存')
  } catch (caught) {
    ElMessage.error(caught.message)
  } finally {
    saving.value = false
  }
}

async function saveScopes() {
  saving.value = true
  try {
    selected.value = await apiRequest(`applications/${selected.value.application_id}/scopes`, {
      method: 'PUT', body: { scope_codes: selected.value.scopes.map((item) => item.scope_code) },
    })
    ElMessage.success('Scope 已同步到身份提供方')
  } catch (caught) {
    ElMessage.error(caught.message)
  } finally {
    saving.value = false
  }
}

async function loadScopeOptions() {
  try {
    scopeOptions.value = (await apiRequest('scopes')).items
  } catch {
    scopeOptions.value = []
  }
}

async function credentialAction(environment, action, credential = null) {
  const verb = action === 'create' ? '创建' : action === 'rotate' ? '轮换' : '吊销'
  if (action === 'revoke') {
    await ElMessageBox.confirm(`吊销 ${environment.environment} 环境凭据 ${credential?.client_id || ''} 后，旧密钥不能再获取令牌，平台也会立即拒绝该凭据已签发的服务令牌。`, '确认吊销凭据', {
      confirmButtonText: '确认吊销', cancelButtonText: '取消', type: 'warning',
    })
  } else if (action === 'rotate') {
    await ElMessageBox.confirm(
      `平台将创建一套新凭据，并把当前凭据保留到过渡窗口结束。请先验证并切换调用方，再吊销旧凭据。`,
      '开始无中断轮换',
      { confirmButtonText: '创建新凭据', cancelButtonText: '取消', type: 'warning' },
    )
  }
  saving.value = true
  try {
    const suffix = action === 'create' ? '' : `/${action}`
    const response = await apiRequest(
      `applications/${selected.value.application_id}/environments/${environment.environment}/credentials${suffix}`,
      {
        method: 'POST',
        body: action === 'revoke' ? { credential_id: credential?.credential_id, force: false } : undefined,
      },
    )
    if (response.client_secret) {
      oneTimeSecret.value = response
      secretVisible.value = true
    }
    await loadDetail(selected.value.application_id)
    ElMessage.success(`凭据已${verb}`)
  } catch (caught) {
    ElMessage.error(caught.message)
  } finally {
    saving.value = false
  }
}

function closeSecret() {
  oneTimeSecret.value = null
  secretVisible.value = false
}

function openRelease(environment) {
  Object.assign(releaseForm, { environment: environment.environment, version: environment.version, activate: true })
  releaseVisible.value = true
}

async function submitRelease() {
  saving.value = true
  try {
    await apiRequest(
      `applications/${selected.value.application_id}/environments/${releaseForm.environment}/releases`,
      { method: 'POST', body: { version: releaseForm.version, activate: releaseForm.activate } },
    )
    await loadDetail(selected.value.application_id)
    releaseVisible.value = false
    ElMessage.success('发布记录已创建')
  } catch (caught) {
    ElMessage.error(caught.message)
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadApplications(), loadScopeOptions()])
})
</script>

<template>
  <div class="page-shell">
    <PageHeader title="应用中心" description="管理独立应用公开接入元数据，平台不持有业务数据。">
      <el-input v-model="keyword" clearable prefix-icon="Search" placeholder="搜索应用、编号或负责人" style="width: 280px" @keyup.enter="loadApplications" @clear="loadApplications" />
      <el-select v-model="statusFilter" clearable placeholder="全部状态" style="width: 135px" @change="loadApplications">
        <el-option v-for="item in ['DRAFT', 'ACTIVE', 'DISABLED', 'RETIRED']" :key="item" :label="item" :value="item" />
      </el-select>
      <template #actions>
        <el-button @click="$router.push('/platform/developer')"><el-icon><Document /></el-icon>接入规范</el-button>
        <el-button v-if="canCreate" type="primary" @click="createVisible = true"><el-icon><Plus /></el-icon>注册应用</el-button>
      </template>
    </PageHeader>

    <section class="surface-panel page-section list-panel">
      <ApiState :loading="loading" :error="error" :empty="!applications.length" empty-text="没有符合条件的应用" @retry="loadApplications">
        <el-table :data="applications" style="width: 100%" @row-click="(row) => loadDetail(row.application_id)">
          <el-table-column prop="name" label="应用" min-width="220">
            <template #default="scope"><strong>{{ scope.row.name }}</strong><small class="subline mono">{{ scope.row.application_id }}</small></template>
          </el-table-column>
          <el-table-column prop="owner" label="负责人" min-width="140" />
          <el-table-column label="能力" min-width="280">
            <template #default="scope"><el-tag v-for="item in scope.row.capabilities" :key="item" size="small" effect="plain" class="inline-tag">{{ item }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="environment_count" label="环境" width="80" align="center" />
          <el-table-column label="Scope" width="90" align="center"><template #default="scope">{{ scope.row.scopes.length }}</template></el-table-column>
          <el-table-column prop="updated_at" label="更新时间" width="170"><template #default="scope">{{ formatTime(scope.row.updated_at) }}</template></el-table-column>
          <el-table-column prop="status" label="状态" width="110"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column>
          <el-table-column label="操作" width="90" fixed="right"><template #default="scope"><el-button type="primary" link @click.stop="loadDetail(scope.row.application_id)">管理</el-button></template></el-table-column>
        </el-table>
      </ApiState>
    </section>

    <el-dialog v-model="createVisible" title="注册独立应用" width="620px">
      <el-form label-position="top" :model="createForm">
        <div class="form-grid"><el-form-item label="应用编号" required><el-input v-model="createForm.application_id" placeholder="lowercase-app-id" /></el-form-item><el-form-item label="应用名称" required><el-input v-model="createForm.name" /></el-form-item></div>
        <el-form-item label="说明" required><el-input v-model="createForm.description" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="负责人" required><el-input v-model="createForm.owner" /></el-form-item>
        <el-form-item label="接入能力"><el-checkbox-group v-model="createForm.capabilities"><el-checkbox v-for="item in capabilityOptions" :key="item[0]" :value="item[0]" :disabled="item[0] === 'API_CLIENT'">{{ item[1] }}</el-checkbox></el-checkbox-group></el-form-item>
      </el-form>
      <template #footer><el-button @click="createVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="submitCreate">注册应用</el-button></template>
    </el-dialog>

    <el-drawer v-model="detailVisible" :title="selected?.name || '应用详情'" size="min(860px, 96vw)">
      <ApiState :loading="detailLoading" :empty="!selected">
        <template v-if="selected">
          <div class="detail-heading"><div><code>{{ selected.application_id }}</code><p>{{ selected.description }}</p></div><el-button v-if="canEditSelected" @click="openEdit">编辑应用</el-button></div>
          <el-descriptions :column="3" border><el-descriptions-item label="负责人">{{ selected.owner }}</el-descriptions-item><el-descriptions-item label="状态"><StatusTag :status="selected.status" /></el-descriptions-item><el-descriptions-item label="更新时间">{{ formatTime(selected.updated_at) }}</el-descriptions-item></el-descriptions>

          <div class="drawer-section"><div class="section-heading"><div><h3>环境与凭据</h3><p>轮换先创建新凭据；旧凭据在过渡窗口内保持可用，验证并切换调用方后再吊销。</p></div><el-button v-if="canEditSelected" @click="openEnvironment()">新增环境</el-button></div>
            <el-table :data="selected.environments" style="width: 100%">
              <el-table-column prop="environment" label="环境" width="95" />
              <el-table-column label="入口" min-width="220"><template #default="scope"><a :href="scope.row.portal_url" target="_blank" class="entry-link">{{ scope.row.portal_url }}</a><small class="subline">{{ scope.row.version }}</small></template></el-table-column>
              <el-table-column label="凭据" min-width="250"><template #default="scope"><template v-if="scope.row.credentials?.length"><div v-for="item in scope.row.credentials" :key="item.credential_id" class="credential-row"><span class="mono">{{ item.client_id }}</span><div class="credential-meta"><StatusTag :status="item.status" /><small>v{{ item.version }}<template v-if="item.revoke_after"> · {{ formatTime(item.revoke_after) }} 后可吊销</template></small></div><el-button v-if="canRevokeSelected && item.status === 'DRAINING'" type="danger" link size="small" @click="credentialAction(scope.row, 'revoke', item)">吊销旧凭据</el-button></div></template><span v-else class="muted">未创建</span></template></el-table-column>
              <el-table-column label="操作" width="220" fixed="right"><template #default="scope"><el-button v-if="canEditSelected" link @click="openEnvironment(scope.row)">配置</el-button><el-button v-if="canEditSelected" link @click="openRelease(scope.row)">发布</el-button><el-button v-if="canRotateSelected && !scope.row.credential" type="primary" link @click="credentialAction(scope.row, 'create')">创建凭据</el-button><el-button v-if="canRotateSelected && scope.row.credential?.status === 'ACTIVE' && !scope.row.credentials?.some((item) => item.status === 'DRAINING')" type="primary" link @click="credentialAction(scope.row, 'rotate')">开始轮换</el-button></template></el-table-column>
            </el-table>
          </div>

          <div class="drawer-section"><div class="section-heading"><div><h3>授权 Scope</h3><p>仅登记应用实际需要的公共 API Scope。</p></div><el-button v-if="canEditSelected" type="primary" :loading="saving" @click="saveScopes">保存 Scope</el-button></div>
            <el-select v-model="selected.scopes" :disabled="!canEditSelected" multiple value-key="scope_code" style="width: 100%" placeholder="选择 Scope"><el-option v-for="item in scopeOptions" :key="item.scope_code" :label="`${item.name} · ${item.scope_code}`" :value="item" /></el-select>
          </div>

          <div class="drawer-section"><div class="section-heading"><div><h3>发布记录</h3><p>版本属于公开接入配置，不代表业务版本。</p></div></div>
            <el-table :data="selected.releases" style="width: 100%"><el-table-column prop="environment" label="环境" width="100" /><el-table-column prop="version" label="版本" min-width="130" /><el-table-column prop="status" label="状态" width="110"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column><el-table-column prop="created_at" label="创建时间" min-width="180"><template #default="scope">{{ formatTime(scope.row.created_at) }}</template></el-table-column></el-table>
          </div>
        </template>
      </ApiState>
    </el-drawer>

    <el-dialog v-model="appEditVisible" title="编辑应用元数据" width="620px"><el-form label-position="top"><el-form-item label="名称"><el-input v-model="appForm.name" /></el-form-item><el-form-item label="说明"><el-input v-model="appForm.description" type="textarea" :rows="3" /></el-form-item><div class="form-grid"><el-form-item label="负责人"><el-input v-model="appForm.owner" /></el-form-item><el-form-item label="状态"><el-select v-model="appForm.status" style="width: 100%"><el-option v-for="item in ['DRAFT', 'ACTIVE', 'DISABLED', 'RETIRED']" :key="item" :value="item" /></el-select></el-form-item></div><el-form-item label="能力"><el-checkbox-group v-model="appForm.capabilities"><el-checkbox v-for="item in capabilityOptions" :key="item[0]" :value="item[0]" :disabled="item[0] === 'API_CLIENT'">{{ item[1] }}</el-checkbox></el-checkbox-group></el-form-item></el-form><template #footer><el-button @click="appEditVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="submitEdit">保存</el-button></template></el-dialog>

    <el-dialog v-model="environmentVisible" title="配置应用环境" width="680px"><el-form label-position="top"><div class="form-grid"><el-form-item label="环境标识" required><el-input v-model="environmentForm.environment" :disabled="selected?.environments.some((item) => item.environment === environmentForm.environment)" /></el-form-item><el-form-item label="版本" required><el-input v-model="environmentForm.version" /></el-form-item></div><el-form-item label="门户入口" required><el-input v-model="environmentForm.portal_url" /></el-form-item><el-form-item label="API 地址" required><el-input v-model="environmentForm.api_base_url" /></el-form-item><el-form-item label="健康检查" required><el-input v-model="environmentForm.health_url" /></el-form-item><el-form-item label="OIDC 回调地址（每行一个）" required><el-input v-model="environmentForm.redirect_text" type="textarea" :rows="3" /></el-form-item><el-form-item label="状态"><el-switch v-model="environmentForm.status" active-value="ACTIVE" inactive-value="DISABLED" /></el-form-item></el-form><template #footer><el-button @click="environmentVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="submitEnvironment">保存环境</el-button></template></el-dialog>

    <el-dialog v-model="releaseVisible" title="创建接入配置版本" width="460px"><el-form label-position="top"><el-form-item label="环境"><el-input v-model="releaseForm.environment" disabled /></el-form-item><el-form-item label="语义版本"><el-input v-model="releaseForm.version" /></el-form-item><el-form-item label="创建后激活"><el-switch v-model="releaseForm.activate" /></el-form-item></el-form><template #footer><el-button @click="releaseVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="submitRelease">创建版本</el-button></template></el-dialog>

    <el-dialog :model-value="secretVisible" title="保存一次性客户端密钥" width="620px" :close-on-click-modal="false" @close="closeSecret"><el-alert type="warning" :closable="false" title="关闭后平台无法再次显示此密钥，请立即保存到应用的密钥管理系统。" /><el-descriptions v-if="oneTimeSecret" :column="1" border class="secret-details"><el-descriptions-item label="Client ID"><code>{{ oneTimeSecret.client_id }}</code></el-descriptions-item><el-descriptions-item label="Client Secret"><code class="secret-value">{{ oneTimeSecret.client_secret }}</code></el-descriptions-item><el-descriptions-item label="Issuer"><code>{{ oneTimeSecret.issuer }}</code></el-descriptions-item></el-descriptions><template #footer><el-button type="primary" @click="closeSecret">我已安全保存</el-button></template></el-dialog>
  </div>
</template>

<style scoped>
.list-panel { min-height: 520px; overflow: hidden; }
.subline { display: block; margin-top: 4px; color: var(--ink-500); font-size: 11px; font-weight: 400; }
.inline-tag { margin: 2px 5px 2px 0; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.detail-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 18px; }
.detail-heading p { margin: 7px 0 0; color: var(--ink-500); line-height: 1.6; }
.drawer-section { margin-top: 28px; }
.entry-link { color: var(--accent-600); word-break: break-all; }
.secret-details { margin-top: 18px; }
.secret-value { user-select: all; word-break: break-all; }
.credential-row + .credential-row { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line-soft); }
.credential-meta { display: flex; align-items: center; gap: 7px; margin-top: 5px; color: var(--ink-500); }
@media (max-width: 650px) { .form-grid { grid-template-columns: 1fr; } }
</style>
