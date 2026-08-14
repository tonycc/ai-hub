<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import ApiState from '../components/ApiState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { apiRequest, queryString } from '../services/platformApi'
import { usePortalSession } from '../stores/session'

const session = usePortalSession()
const activeTab = ref('messages')
const loading = ref(false)
const error = ref(null)
const messages = ref([])
const configurations = ref([])
const applications = ref([])
const users = ref([])
const statusFilter = ref('')
const configVisible = ref(false)
const testVisible = ref(false)
const saving = ref(false)
const configForm = reactive({ application_id: '', enabled: true, sender_name: '' })
const testForm = reactive({ application_id: '', recipient_user_id: '', subject: '', body: '', idempotency_key: '' })
const canWriteAny = computed(() => session.hasPermission('platform.notification.write'))

function formatTime(value) { return value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—' }

async function loadAll() {
  loading.value = true; error.value = null
  try {
    const [messageResponse, configResponse, appResponse] = await Promise.all([
      apiRequest(`notifications${queryString({ status: statusFilter.value })}`),
      apiRequest('notification-configurations'), apiRequest('applications'),
    ])
    messages.value = messageResponse.items
    configurations.value = configResponse.items
    applications.value = appResponse.items
  } catch (caught) { error.value = caught } finally { loading.value = false }
}

async function loadRecipients(applicationId) {
  if (!applicationId || !session.hasPermission('platform.notification.write', applicationId)) {
    users.value = []
    return
  }
  try { users.value = (await apiRequest(`applications/${applicationId}/notification-recipients`)).items } catch { users.value = [] }
}

function openConfig(row = null) {
  Object.assign(configForm, row ? { application_id: row.application_id, enabled: row.enabled, sender_name: row.sender_name } : { application_id: applications.value[0]?.application_id || '', enabled: true, sender_name: 'AI Hub Platform' })
  configVisible.value = true
}

async function saveConfig() {
  saving.value = true
  try {
    await apiRequest(`applications/${configForm.application_id}/notification-configurations/IN_APP`, {
      method: 'PUT', body: { enabled: configForm.enabled, sender_name: configForm.sender_name, configuration: { delivery_mode: 'LOCAL_REFERENCE' } },
    })
    configVisible.value = false; ElMessage.success('站内测试通知配置已保存'); await loadAll()
  } catch (caught) { ElMessage.error(caught.message) } finally { saving.value = false }
}

async function openTest() {
  const applicationId = applications.value.find((item) => session.hasPermission('platform.notification.write', item.application_id))?.application_id || ''
  await loadRecipients(applicationId)
  Object.assign(testForm, { application_id: applicationId, recipient_user_id: users.value[0]?.user_id || '', subject: '平台接入测试通知', body: '用于验证站内通知配置与审计链路。', idempotency_key: crypto.randomUUID() })
  testVisible.value = true
}

async function changeTestApplication(applicationId) {
  await loadRecipients(applicationId)
  testForm.recipient_user_id = users.value[0]?.user_id || ''
}

async function sendTest() {
  saving.value = true
  try {
    await apiRequest(`applications/${testForm.application_id}/notifications/test`, {
      method: 'POST', body: { recipient_user_id: testForm.recipient_user_id, subject: testForm.subject, body: testForm.body, idempotency_key: testForm.idempotency_key, payload: { source: 'portal-uat' } },
    })
    testVisible.value = false; ElMessage.success('站内测试通知已记录为送达'); await loadAll()
  } catch (caught) { ElMessage.error(caught.message) } finally { saving.value = false }
}

onMounted(loadAll)
</script>

<template>
  <div class="page-shell">
    <PageHeader title="通知中心" description="配置站内测试通知并验证幂等、状态和审计链路；当前不包含邮件、短信或协作软件等外部通道。">
      <template #tabs><div class="management-tabs"><button :class="{ active: activeTab === 'messages' }" @click="activeTab = 'messages'">站内送达记录 <em>{{ messages.length }}</em></button><button :class="{ active: activeTab === 'configurations' }" @click="activeTab = 'configurations'">站内通道配置 <em>{{ configurations.length }}</em></button></div></template>
      <el-select v-if="activeTab === 'messages'" v-model="statusFilter" clearable placeholder="全部状态" style="width: 145px" @change="loadAll"><el-option v-for="item in ['PENDING','DELIVERED','FAILED']" :key="item" :value="item" /></el-select>
      <template #actions><el-button @click="loadAll"><el-icon><Refresh /></el-icon>刷新</el-button><el-button v-if="canWriteAny && activeTab === 'configurations'" @click="openConfig()"><el-icon><Setting /></el-icon>新增配置</el-button><el-button v-if="canWriteAny" type="primary" :disabled="!applications.some((item) => session.hasPermission('platform.notification.write', item.application_id))" @click="openTest"><el-icon><Promotion /></el-icon>发送测试通知</el-button></template>
    </PageHeader>
    <section class="surface-panel channel-scope page-section" aria-label="通知通道边界"><span><el-icon><Bell /></el-icon></span><div><strong>当前仅提供站内测试通道</strong><small>用于接入验收和审计链路验证。邮件、短信、Teams、企业微信等真实投递供应商需要单独选型、配置并验收，当前状态不代表外部收件人已收到消息。</small></div><dl><div><dt>通道编码</dt><dd><code>IN_APP</code></dd></div><div><dt>投递模式</dt><dd><code>LOCAL_REFERENCE</code></dd></div></dl></section>
    <section class="surface-panel page-section list-panel"><ApiState :loading="loading" :error="error" :empty="activeTab === 'messages' ? !messages.length : !configurations.length" :empty-text="activeTab === 'messages' ? '暂无站内测试通知记录' : '暂无站内测试通知配置'" @retry="loadAll">
      <el-table v-if="activeTab === 'messages'" :data="messages" style="width: 100%"><el-table-column prop="subject" label="主题" min-width="240" /><el-table-column prop="application_name" label="应用" min-width="160"><template #default="scope">{{ scope.row.application_name || scope.row.application_id }}</template></el-table-column><el-table-column prop="recipient_name" label="收件人" min-width="150"><template #default="scope">{{ scope.row.recipient_name || scope.row.recipient_user_id }}</template></el-table-column><el-table-column prop="requested_at" label="请求时间" width="175"><template #default="scope">{{ formatTime(scope.row.requested_at) }}</template></el-table-column><el-table-column prop="status" label="状态" width="125"><template #default="scope"><el-tag v-if="scope.row.status === 'DELIVERED'" type="success" size="small" effect="light">测试已记录</el-tag><StatusTag v-else :status="scope.row.status" /></template></el-table-column><el-table-column prop="failure_reason" label="失败原因" min-width="180"><template #default="scope">{{ scope.row.failure_reason || '—' }}</template></el-table-column></el-table>
      <el-table v-else :data="configurations" style="width: 100%"><el-table-column prop="application_name" label="应用" min-width="200"><template #default="scope"><strong>{{ scope.row.application_name || scope.row.application_id }}</strong><small class="subline mono">{{ scope.row.application_id }}</small></template></el-table-column><el-table-column prop="channel" label="渠道" width="150"><template #default><span>站内测试 <code>IN_APP</code></span></template></el-table-column><el-table-column prop="sender_name" label="发送方名称" min-width="180" /><el-table-column label="状态" width="120"><template #default="scope"><StatusTag :status="scope.row.enabled ? 'ACTIVE' : 'DISABLED'" /></template></el-table-column><el-table-column prop="updated_at" label="更新时间" width="175"><template #default="scope">{{ formatTime(scope.row.updated_at) }}</template></el-table-column><el-table-column v-if="canWriteAny" label="操作" width="90" fixed="right"><template #default="scope"><el-button v-if="session.hasPermission('platform.notification.write', scope.row.application_id)" type="primary" link @click="openConfig(scope.row)">编辑</el-button></template></el-table-column></el-table>
    </ApiState></section>

    <el-dialog v-model="configVisible" title="配置站内测试通知" width="520px"><el-form label-position="top"><el-form-item label="应用" required><el-select v-model="configForm.application_id" style="width: 100%"><el-option v-for="item in applications.filter((app) => session.hasPermission('platform.notification.write', app.application_id))" :key="item.application_id" :label="item.name" :value="item.application_id" /></el-select></el-form-item><el-form-item label="发送方名称" required><el-input v-model="configForm.sender_name" /></el-form-item><el-form-item label="启用站内测试通道"><el-switch v-model="configForm.enabled" /></el-form-item></el-form><template #footer><el-button @click="configVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveConfig">保存</el-button></template></el-dialog>
    <el-dialog v-model="testVisible" title="发送站内测试通知" width="560px"><el-form label-position="top"><el-form-item label="应用" required><el-select v-model="testForm.application_id" style="width: 100%" @change="changeTestApplication"><el-option v-for="item in applications.filter((app) => session.hasPermission('platform.notification.write', app.application_id))" :key="item.application_id" :label="item.name" :value="item.application_id" /></el-select></el-form-item><el-form-item label="站内收件人" required><el-select v-model="testForm.recipient_user_id" filterable style="width: 100%"><el-option v-for="item in users" :key="item.user_id" :label="`${item.display_name} · ${item.subject}`" :value="item.user_id" /></el-select></el-form-item><el-form-item label="主题" required><el-input v-model="testForm.subject" /></el-form-item><el-form-item label="正文" required><el-input v-model="testForm.body" type="textarea" :rows="4" /></el-form-item><el-form-item label="幂等键"><el-input v-model="testForm.idempotency_key" disabled /></el-form-item></el-form><template #footer><el-button @click="testVisible = false">取消</el-button><el-button type="primary" :loading="saving" :disabled="!testForm.recipient_user_id" @click="sendTest">发送站内测试</el-button></template></el-dialog>
  </div>
</template>

<style scoped>.management-tabs{display:flex;gap:24px}.management-tabs button{position:relative;height:46px;padding:0;border:0;color:var(--ink-500);background:transparent;font-size:15px;cursor:pointer}.management-tabs button.active{color:var(--ink-900);font-weight:650}.management-tabs button.active::after{position:absolute;right:0;bottom:0;left:0;height:2px;background:var(--accent-500);content:''}.management-tabs em{margin-left:5px;color:#89959d;font-size:11px;font-style:normal}.channel-scope{display:grid;grid-template-columns:42px minmax(0,1fr) auto;align-items:center;gap:12px;padding:14px 16px}.channel-scope>span{display:grid;width:40px;height:40px;border-radius:8px;color:#416f86;background:#edf4f7;font-size:18px;place-items:center}.channel-scope>div{display:grid;gap:4px}.channel-scope strong{color:var(--ink-900);font-size:13px}.channel-scope small{max-width:760px;color:var(--ink-500);font-size:11px;line-height:1.5}.channel-scope dl{display:flex;gap:22px;margin:0}.channel-scope dl div{display:grid;gap:4px}.channel-scope dt{color:var(--ink-500);font-size:10px}.channel-scope dd{margin:0;color:var(--ink-700);font-size:11px}.list-panel{min-height:460px;overflow:hidden}.subline{display:block;margin-top:4px;color:var(--ink-500);font-size:11px}code{color:#416c83}@media(max-width:850px){.channel-scope{grid-template-columns:42px 1fr}.channel-scope dl{grid-column:2}}@media(max-width:560px){.channel-scope dl{display:grid;gap:9px}}</style>
