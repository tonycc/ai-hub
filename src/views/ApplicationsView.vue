<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import ApiState from '../components/ApiState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { apiRequest, queryString } from '../services/platformApi'
import { usePortalSession } from '../stores/session'

const session = usePortalSession()
const route = useRoute()
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
const platformUsers = ref([])

const capabilityOptions = [
  ['API_CLIENT', 'API 客户端'],
  ['DATA_INGEST', '增量数据接入'],
]

const statusOptions = [
  ['DRAFT', '草稿'],
  ['ACTIVE', '启用'],
  ['DISABLED', '停用'],
  ['RETIRED', '已退役'],
]

const createForm = reactive({
  application_id: '', name: '', description: '', owner_id: '', capabilities: ['API_CLIENT'],
})
const createIdManual = ref(false)
const appForm = reactive({ name: '', description: '', owner_id: '', owner_display: '', status: 'DRAFT', capabilities: [] })
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

function hasLiveCredentials(environment) {
  return (environment.credentials || []).some(
    (item) => item.status === 'ACTIVE' || item.status === 'DRAINING',
  )
}

function isRevokeEligible(credential) {
  if (credential.status !== 'DRAINING') return true
  if (!credential.revoke_after) return true
  return new Date(credential.revoke_after) <= new Date()
}

function generateApplicationId(name) {
  let id = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 63)
    // The backend APPLICATION_ID_PATTERN requires 3-63 chars and forbids a
    // trailing hyphen, so re-trim after truncation.
    .replace(/-+$/g, '')
  while (id.length > 0 && id.length < 3) {
    id = `${id}-app`
  }
  return id || 'my-app'
}

function onNameInput() {
  if (!createIdManual.value) {
    createForm.application_id = generateApplicationId(createForm.name)
  }
}

function onIdInput() {
  createIdManual.value = true
}

async function loadPlatformUsers() {
  try {
    // The backend only accepts an ACTIVE owner, so never offer disabled
    // accounts (including the retired seed users) in the selector.
    platformUsers.value = (await apiRequest('users?status=ACTIVE')).items
  } catch {
    platformUsers.value = []
  }
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
  // 表单级必填校验，避免空 owner_id 直接打到后端变成数据库 500
  if (!createForm.application_id.trim()) {
    ElMessage.error('应用编号不能为空')
    return
  }
  if (!createForm.name.trim()) {
    ElMessage.error('应用名称不能为空')
    return
  }
  if (!createForm.description.trim()) {
    ElMessage.error('说明不能为空')
    return
  }
  if (!createForm.owner_id) {
    ElMessage.error('请选择负责人')
    return
  }
  saving.value = true
  try {
    const body = {
      application_id: createForm.application_id,
      name: createForm.name,
      description: createForm.description,
      owner_id: createForm.owner_id,
      capabilities: createForm.capabilities,
    }
    const created = await apiRequest('applications', { method: 'POST', body })
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
  // Use the stable owner_id returned by the detail endpoint; the display
  // string is presentation-only and must never be used to reverse-lookup the
  // user (renames or empty emails would silently change the owner).
  Object.assign(appForm, {
    name: selected.value.name,
    description: selected.value.description,
    owner_id: selected.value.owner_id || '',
    owner_display: selected.value.owner,
    status: selected.value.status,
    capabilities: [...selected.value.capabilities],
  })
  appEditVisible.value = true
}

async function submitEdit() {
  saving.value = true
  try {
    const body = { ...appForm }
    delete body.owner_display
    // 无负责人管理权限时保留原值
    if (!body.owner_id) {
      delete body.owner_id
    }
    selected.value = await apiRequest(`applications/${selected.value.application_id}`, {
      method: 'PUT', body,
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
    ElMessage.success('权限范围已同步到身份提供方')
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
  const verb = action === 'create' ? '创建' : action === 'rotate' ? '生成新密钥' : '删除旧密钥'
  if (action === 'revoke') {
    if (credential?.status === 'DRAINING') {
      const now = new Date()
      const revokeAfter = credential.revoke_after ? new Date(credential.revoke_after) : null
      if (revokeAfter && revokeAfter > now) {
        ElMessage.warning(`密钥处于过渡窗口期，${formatTime(revokeAfter)} 后才可删除。`)
        return
      }
    }
    await ElMessageBox.confirm(`删除 ${environment.environment} 环境密钥 ${credential?.client_id || ''} 后，旧密钥不能再获取令牌，平台也会立即拒绝该密钥已签发的服务令牌。`, '确认删除密钥', {
      confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning',
    })
  } else if (action === 'rotate') {
    await ElMessageBox.confirm(
      `平台将生成一套新密钥，并把当前密钥保留到过渡窗口结束。请先验证并切换调用方，再删除旧密钥。`,
      '生成新密钥',
      { confirmButtonText: '生成新密钥', cancelButtonText: '取消', type: 'warning' },
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
    ElMessage.success(`密钥已${verb}`)
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
  await Promise.all([loadApplications(), loadScopeOptions(), loadPlatformUsers()])
  // 如果 URL 带 app 参数，自动打开对应应用详情
  const appId = route.query.app
  if (appId && typeof appId === 'string') {
    await loadDetail(appId)
  }
})
</script>

<template>
  <div class="page-shell">
    <PageHeader title="应用中心" description="管理独立应用公开接入元数据，平台不持有业务数据。">
      <el-input v-model="keyword" clearable prefix-icon="Search" placeholder="搜索应用、编号或负责人" style="width: 280px" @keyup.enter="loadApplications" @clear="loadApplications" />
      <el-select v-model="statusFilter" clearable placeholder="全部状态" style="width: 135px" @change="loadApplications">
        <el-option v-for="item in statusOptions" :key="item[0]" :label="item[1]" :value="item[0]" />
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
            <template #default="scope"><el-tag v-for="item in scope.row.capabilities" :key="item" size="small" effect="plain" class="inline-tag">{{ capabilityOptions.find((option) => option[0] === item)?.[1] || item }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="environment_count" label="环境" width="80" align="center" />
          <el-table-column label="权限范围" width="90" align="center"><template #default="scope">{{ scope.row.scopes.length }}</template></el-table-column>
          <el-table-column prop="updated_at" label="更新时间" width="170"><template #default="scope">{{ formatTime(scope.row.updated_at) }}</template></el-table-column>
          <el-table-column prop="status" label="状态" width="110"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column>
          <el-table-column label="操作" width="90" fixed="right"><template #default="scope"><el-button type="primary" link @click.stop="loadDetail(scope.row.application_id)">管理</el-button></template></el-table-column>
        </el-table>
      </ApiState>
    </section>

    <el-drawer v-model="createVisible" title="注册独立应用" size="min(620px, 96vw)">
      <el-form label-position="top" :model="createForm">
        <div class="form-grid">
          <el-form-item label="应用名称" required>
            <el-input v-model="createForm.name" @input="onNameInput" />
          </el-form-item>
          <el-form-item label="应用编号" required>
            <el-input v-model="createForm.application_id" placeholder="lowercase-app-id" @input="onIdInput">
              <template #append><el-button @click="createForm.application_id = generateApplicationId(createForm.name); createIdManual = false">自动生成</el-button></template>
            </el-input>
          </el-form-item>
        </div>
        <el-form-item label="说明" required><el-input v-model="createForm.description" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="负责人" required>
          <el-select v-model="createForm.owner_id" placeholder="选择平台用户" style="width: 100%">
            <el-option v-for="item in platformUsers" :key="item.user_id" :label="`${item.display_name} <${item.email}>`" :value="item.user_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="接入能力"><el-checkbox-group v-model="createForm.capabilities"><el-checkbox v-for="item in capabilityOptions" :key="item[0]" :value="item[0]" :disabled="item[0] === 'API_CLIENT'">{{ item[1] }}</el-checkbox></el-checkbox-group></el-form-item>
      </el-form>
      <template #footer><el-button @click="createVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="submitCreate">注册应用</el-button></template>
    </el-drawer>

    <el-drawer v-model="detailVisible" :title="selected?.name || '应用详情'" size="min(860px, 96vw)">
      <ApiState :loading="detailLoading" :empty="!selected">
        <template v-if="selected">
          <div class="detail-heading"><div><h3 class="detail-heading__name">{{ selected.name }}</h3><p>{{ selected.description }}</p></div><el-button v-if="canEditSelected" @click="openEdit">编辑应用</el-button></div>
          <el-descriptions :column="3" border><el-descriptions-item label="负责人">{{ selected.owner }}</el-descriptions-item><el-descriptions-item label="状态"><StatusTag :status="selected.status" /></el-descriptions-item><el-descriptions-item label="更新时间">{{ formatTime(selected.updated_at) }}</el-descriptions-item></el-descriptions>

          <div class="drawer-section"><div class="section-heading"><div><h3>环境</h3><p>配置应用的部署环境和接入地址。</p></div><el-button v-if="canEditSelected" @click="openEnvironment()">新增环境</el-button></div>
            <el-table :data="selected.environments" style="width: 100%">
              <el-table-column prop="environment" label="环境" width="95" />
              <el-table-column label="入口" min-width="220"><template #default="scope"><a :href="scope.row.portal_url" target="_blank" class="entry-link">{{ scope.row.portal_url }}</a><small class="subline">{{ scope.row.version }}</small></template></el-table-column>
              <el-table-column prop="api_base_url" label="API 地址" min-width="220"><template #default="scope"><code class="mono">{{ scope.row.api_base_url }}</code></template></el-table-column>
              <el-table-column label="操作" width="180" fixed="right"><template #default="scope"><el-button v-if="canEditSelected" link @click="openEnvironment(scope.row)">配置</el-button><el-button v-if="canEditSelected" link @click="openRelease(scope.row)">发布</el-button></template></el-table-column>
            </el-table>
          </div>

          <div class="drawer-section"><div class="section-heading"><div><h3>密钥管理</h3><p>生成新密钥；旧密钥在过渡窗口内保持可用，验证并切换调用方后再删除。</p></div></div>
            <el-table :data="selected.environments" style="width: 100%">
              <el-table-column prop="environment" label="环境" width="95" />
              <el-table-column label="密钥" min-width="280"><template #default="scope"><template v-if="scope.row.credentials?.length"><div v-for="item in scope.row.credentials" :key="item.credential_id" class="credential-row"><span class="mono">{{ item.client_id }}</span><div class="credential-meta"><StatusTag :status="item.status" :label="item.status === 'REVOKED' ? '已删除' : ''" /><small>v{{ item.version }}<template v-if="item.revoke_after"> · {{ formatTime(item.revoke_after) }} 后可删除</template></small></div><el-button v-if="canRevokeSelected && item.status === 'DRAINING' && isRevokeEligible(item)" type="danger" link size="small" @click="credentialAction(scope.row, 'revoke', item)">删除旧密钥</el-button><el-tag v-else-if="item.status === 'DRAINING'" type="warning" size="small" effect="plain">过渡中</el-tag></div></template><span v-else class="muted">未创建</span></template></el-table-column>
              <el-table-column label="操作" width="200" fixed="right"><template #default="scope"><el-button v-if="canRotateSelected && !hasLiveCredentials(scope.row)" type="primary" link @click="credentialAction(scope.row, 'create')">创建密钥</el-button><el-button v-if="canRotateSelected && scope.row.credentials?.some((item) => item.status === 'ACTIVE') && !scope.row.credentials?.some((item) => item.status === 'DRAINING')" type="primary" link @click="credentialAction(scope.row, 'rotate')">生成新密钥</el-button></template></el-table-column>
            </el-table>
          </div>

          <div class="drawer-section"><div class="section-heading"><div><h3>权限范围</h3><p>仅登记应用实际需要的公共 API 权限范围。</p></div><el-button v-if="canEditSelected" type="primary" :loading="saving" @click="saveScopes">保存权限范围</el-button></div>
            <el-select v-model="selected.scopes" :disabled="!canEditSelected" multiple value-key="scope_code" style="width: 100%" placeholder="选择权限范围"><el-option v-for="item in scopeOptions" :key="item.scope_code" :label="`${item.name} · ${item.scope_code}`" :value="item" /></el-select>
          </div>

          <div class="drawer-section"><div class="section-heading"><div><h3>发布记录</h3><p>版本属于公开接入配置，不代表业务版本。</p></div></div>
            <el-table :data="selected.releases" style="width: 100%"><el-table-column prop="environment" label="环境" width="100" /><el-table-column prop="version" label="版本" min-width="130" /><el-table-column prop="status" label="状态" width="110"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column><el-table-column prop="created_at" label="创建时间" min-width="180"><template #default="scope">{{ formatTime(scope.row.created_at) }}</template></el-table-column></el-table>
          </div>
        </template>
      </ApiState>
    </el-drawer>

    <el-drawer v-model="appEditVisible" title="编辑应用元数据" size="min(620px, 96vw)"><el-form label-position="top"><el-form-item label="名称"><el-input v-model="appForm.name" /></el-form-item><el-form-item label="说明"><el-input v-model="appForm.description" type="textarea" :rows="3" /></el-form-item><div class="form-grid"><el-form-item label="负责人"><el-select v-model="appForm.owner_id" placeholder="选择平台用户" style="width: 100%"><el-option v-for="item in platformUsers" :key="item.user_id" :label="`${item.display_name} <${item.email}>`" :value="item.user_id" /></el-select></el-form-item><el-form-item label="状态"><el-select v-model="appForm.status" style="width: 100%"><el-option v-for="item in statusOptions" :key="item[0]" :label="item[1]" :value="item[0]" /></el-select></el-form-item></div><el-form-item label="能力"><el-checkbox-group v-model="appForm.capabilities"><el-checkbox v-for="item in capabilityOptions" :key="item[0]" :value="item[0]" :disabled="item[0] === 'API_CLIENT'">{{ item[1] }}</el-checkbox></el-checkbox-group></el-form-item></el-form><template #footer><el-button @click="appEditVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="submitEdit">保存</el-button></template></el-drawer>

    <el-drawer v-model="environmentVisible" title="配置应用环境" size="min(680px, 96vw)"><el-form label-position="top"><div class="form-grid"><el-form-item label="环境标识" required><el-input v-model="environmentForm.environment" :disabled="selected?.environments.some((item) => item.environment === environmentForm.environment)" /></el-form-item><el-form-item label="版本" required><el-input v-model="environmentForm.version" /></el-form-item></div><el-form-item label="门户入口" required><el-input v-model="environmentForm.portal_url" /></el-form-item><el-form-item label="API 地址" required><el-input v-model="environmentForm.api_base_url" /></el-form-item><el-form-item label="健康检查" required><el-input v-model="environmentForm.health_url" /></el-form-item><el-form-item label="OIDC 回调地址（每行一个）" required><el-input v-model="environmentForm.redirect_text" type="textarea" :rows="3" /></el-form-item><el-form-item label="状态"><el-switch v-model="environmentForm.status" active-value="ACTIVE" inactive-value="DISABLED" /></el-form-item></el-form><template #footer><el-button @click="environmentVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="submitEnvironment">保存环境</el-button></template></el-drawer>

    <el-drawer v-model="releaseVisible" title="创建接入配置版本" size="min(460px, 96vw)"><el-form label-position="top"><el-form-item label="环境"><el-input v-model="releaseForm.environment" disabled /></el-form-item><el-form-item label="语义版本"><el-input v-model="releaseForm.version" /></el-form-item><el-form-item label="创建后激活"><el-switch v-model="releaseForm.activate" /></el-form-item></el-form><template #footer><el-button @click="releaseVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="submitRelease">创建版本</el-button></template></el-drawer>

    <el-drawer :model-value="secretVisible" title="保存一次性客户端密钥" size="min(620px, 96vw)" :close-on-click-modal="false" @close="closeSecret"><el-alert type="warning" :closable="false" title="关闭后平台无法再次显示此密钥，请立即保存到应用的密钥管理系统。" /><el-descriptions v-if="oneTimeSecret" :column="1" border class="secret-details"><el-descriptions-item label="Client ID"><code>{{ oneTimeSecret.client_id }}</code></el-descriptions-item><el-descriptions-item label="Client Secret"><code class="secret-value">{{ oneTimeSecret.client_secret }}</code></el-descriptions-item><el-descriptions-item label="Issuer"><code>{{ oneTimeSecret.issuer }}</code></el-descriptions-item></el-descriptions><template #footer><el-button type="primary" @click="closeSecret">我已安全保存</el-button></template></el-drawer>
  </div>
</template>

<style scoped>
.list-panel { min-height: 520px; overflow: hidden; }
.subline { display: block; margin-top: 4px; color: var(--ink-500); font-size: 11px; font-weight: 400; }
.inline-tag { margin: 2px 5px 2px 0; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.detail-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 18px; }
.detail-heading__name { margin: 0; font-size: var(--font-heading); color: var(--ink-900); }
.detail-heading p { margin: 7px 0 0; color: var(--ink-500); line-height: 1.6; }
.drawer-section { margin-top: 28px; }
.entry-link { color: var(--accent-600); word-break: break-all; }
.secret-details { margin-top: 18px; }
.secret-value { user-select: all; word-break: break-all; }
.credential-row + .credential-row { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line-soft); }
.credential-meta { display: flex; align-items: center; gap: 7px; margin-top: 5px; color: var(--ink-500); }
@media (max-width: 650px) { .form-grid { grid-template-columns: 1fr; } }
</style>
