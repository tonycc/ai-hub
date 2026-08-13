<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import ApiState from '../components/ApiState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { apiRequest } from '../services/platformApi'
import { usePortalSession } from '../stores/session'

const session = usePortalSession()
const loading = ref(false)
const error = ref(null)
const applications = ref([])
const applicationId = ref('')
const activeTab = ref('permissions')
const permissions = ref([])
const roles = ref([])
const assignments = ref([])
const users = ref([])
const permissionVisible = ref(false)
const roleVisible = ref(false)
const assignmentVisible = ref(false)
const saving = ref(false)
const permissionForm = reactive({ permission_code: '', name: '', description: '', risk_level: 'LOW' })
const roleForm = reactive({ name: '', description: '', permission_codes: [] })
const assignmentForm = reactive({ user_id: '', role_id: '', data_scope_type: 'OWNED', data_scope_text: '{"owner_subject": "self"}' })

const canWrite = computed(() => applicationId.value
  && session.hasPermission('platform.authorization.write', applicationId.value))

async function loadApplications() {
  try {
    applications.value = (await apiRequest('applications')).items
    if (!applicationId.value) applicationId.value = applications.value[0]?.application_id || ''
  } catch (caught) { error.value = caught }
}

async function loadResources() {
  if (!applicationId.value) return
  loading.value = true
  error.value = null
  try {
    const [permissionResponse, roleResponse, assignmentResponse] = await Promise.all([
      apiRequest(`applications/${applicationId.value}/permissions`),
      apiRequest(`applications/${applicationId.value}/authorization-roles`),
      apiRequest(`applications/${applicationId.value}/authorization-role-assignments`),
    ])
    permissions.value = permissionResponse.items
    roles.value = roleResponse.items
    assignments.value = assignmentResponse.items
  } catch (caught) { error.value = caught } finally { loading.value = false }
}

async function loadUsersIfAllowed() {
  if (!session.hasPermission('platform.identity.read')) return
  try { users.value = (await apiRequest('users')).items } catch { users.value = [] }
}

async function savePermission() {
  saving.value = true
  try {
    await apiRequest(`applications/${applicationId.value}/permissions`, { method: 'POST', body: permissionForm })
    permissionVisible.value = false
    ElMessage.success('权限点已登记')
    await loadResources()
  } catch (caught) { ElMessage.error(caught.message) } finally { saving.value = false }
}

async function saveRole() {
  saving.value = true
  try {
    await apiRequest(`applications/${applicationId.value}/authorization-roles`, { method: 'POST', body: roleForm })
    roleVisible.value = false
    ElMessage.success('应用角色已创建')
    await loadResources()
  } catch (caught) { ElMessage.error(caught.message) } finally { saving.value = false }
}

async function saveAssignment() {
  saving.value = true
  try {
    const dataScope = assignmentForm.data_scope_type === 'GLOBAL' ? {} : JSON.parse(assignmentForm.data_scope_text)
    await apiRequest(`applications/${applicationId.value}/authorization-role-assignments`, {
      method: 'POST',
      body: { user_id: assignmentForm.user_id, role_id: assignmentForm.role_id, data_scope_type: assignmentForm.data_scope_type, data_scope: dataScope },
    })
    assignmentVisible.value = false
    ElMessage.success('应用授权已分配，授权版本已更新')
    await loadResources()
  } catch (caught) { ElMessage.error(caught.message || '数据范围 JSON 无效') } finally { saving.value = false }
}

watch(applicationId, loadResources)
onMounted(async () => { await Promise.all([loadApplications(), loadUsersIfAllowed()]); await loadResources() })
</script>

<template>
  <div class="page-shell">
    <PageHeader title="权限与安全" description="应用权限点、应用角色和数据范围彼此隔离；对象级最终校验仍由独立应用执行。">
      <template #tabs><div class="management-tabs"><button v-for="item in [['permissions','权限点'],['roles','应用角色'],['assignments','用户授权']]" :key="item[0]" :class="{ active: activeTab === item[0] }" @click="activeTab = item[0]">{{ item[1] }}</button></div></template>
      <el-select v-model="applicationId" filterable placeholder="选择应用" style="width: 260px"><el-option v-for="item in applications" :key="item.application_id" :label="`${item.name} · ${item.application_id}`" :value="item.application_id" /></el-select>
      <template #actions><el-button @click="loadResources"><el-icon><Refresh /></el-icon>刷新</el-button><el-button v-if="canWrite && activeTab === 'permissions'" type="primary" @click="permissionVisible = true"><el-icon><Plus /></el-icon>登记权限点</el-button><el-button v-if="canWrite && activeTab === 'roles'" type="primary" @click="roleVisible = true"><el-icon><Plus /></el-icon>创建角色</el-button><el-button v-if="canWrite && activeTab === 'assignments'" type="primary" :disabled="!users.length" @click="assignmentVisible = true"><el-icon><Plus /></el-icon>分配授权</el-button></template>
    </PageHeader>
    <section class="surface-panel page-section list-panel"><ApiState :loading="loading" :error="error" :empty="!applicationId" empty-text="请选择一个应用" @retry="loadResources">
      <el-table v-if="activeTab === 'permissions'" :data="permissions" style="width: 100%"><el-table-column prop="permission_code" label="权限编码" min-width="260"><template #default="scope"><code>{{ scope.row.permission_code }}</code></template></el-table-column><el-table-column prop="name" label="名称" min-width="170" /><el-table-column prop="description" label="说明" min-width="300" /><el-table-column prop="risk_level" label="风险" width="110"><template #default="scope"><el-tag effect="plain" :type="scope.row.risk_level === 'CRITICAL' || scope.row.risk_level === 'HIGH' ? 'danger' : scope.row.risk_level === 'MEDIUM' ? 'warning' : 'info'">{{ scope.row.risk_level }}</el-tag></template></el-table-column><el-table-column prop="status" label="状态" width="110"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column></el-table>
      <el-table v-else-if="activeTab === 'roles'" :data="roles" style="width: 100%"><el-table-column prop="name" label="角色名称" min-width="190" /><el-table-column prop="description" label="说明" min-width="280" /><el-table-column label="权限点" min-width="300"><template #default="scope"><code v-for="item in scope.row.permissions" :key="item" class="permission-code">{{ item }}</code></template></el-table-column><el-table-column prop="assignment_count" label="分配数" width="90" align="center" /><el-table-column prop="status" label="状态" width="110"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column></el-table>
      <el-table v-else :data="assignments" style="width: 100%"><el-table-column prop="display_name" label="用户" min-width="180" /><el-table-column prop="subject" label="身份主体" min-width="210"><template #default="scope"><code>{{ scope.row.subject }}</code></template></el-table-column><el-table-column prop="role_name" label="应用角色" min-width="180" /><el-table-column prop="data_scope_type" label="数据范围" width="160" /><el-table-column label="范围参数" min-width="260"><template #default="scope"><code>{{ JSON.stringify(scope.row.data_scope) }}</code></template></el-table-column></el-table>
    </ApiState></section>

    <el-dialog v-model="permissionVisible" title="登记应用权限点" width="580px"><el-form label-position="top"><el-form-item label="权限编码" required><el-input v-model="permissionForm.permission_code" placeholder="example.record.read" /></el-form-item><el-form-item label="名称" required><el-input v-model="permissionForm.name" /></el-form-item><el-form-item label="说明" required><el-input v-model="permissionForm.description" type="textarea" :rows="3" /></el-form-item><el-form-item label="风险等级"><el-select v-model="permissionForm.risk_level" style="width: 100%"><el-option v-for="item in ['LOW','MEDIUM','HIGH','CRITICAL']" :key="item" :value="item" /></el-select></el-form-item></el-form><template #footer><el-button @click="permissionVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="savePermission">登记</el-button></template></el-dialog>
    <el-dialog v-model="roleVisible" title="创建应用角色" width="600px"><el-form label-position="top"><el-form-item label="角色名称" required><el-input v-model="roleForm.name" /></el-form-item><el-form-item label="说明" required><el-input v-model="roleForm.description" type="textarea" :rows="3" /></el-form-item><el-form-item label="权限点" required><el-select v-model="roleForm.permission_codes" multiple style="width: 100%"><el-option v-for="item in permissions.filter((value) => value.status === 'ACTIVE')" :key="item.permission_code" :label="`${item.name} · ${item.permission_code}`" :value="item.permission_code" /></el-select></el-form-item></el-form><template #footer><el-button @click="roleVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveRole">创建</el-button></template></el-dialog>
    <el-dialog v-model="assignmentVisible" title="分配应用授权" width="600px"><el-form label-position="top"><el-form-item label="用户" required><el-select v-model="assignmentForm.user_id" filterable style="width: 100%"><el-option v-for="item in users" :key="item.user_id" :label="`${item.display_name} · ${item.subject}`" :value="item.user_id" /></el-select></el-form-item><el-form-item label="应用角色" required><el-select v-model="assignmentForm.role_id" style="width: 100%"><el-option v-for="item in roles.filter((value) => value.status === 'ACTIVE')" :key="item.role_id" :label="item.name" :value="item.role_id" /></el-select></el-form-item><el-form-item label="数据范围"><el-select v-model="assignmentForm.data_scope_type" style="width: 100%"><el-option v-for="item in ['GLOBAL','ORGANIZATION','ORGANIZATION_TREE','OWNED','ATTRIBUTE']" :key="item" :value="item" /></el-select></el-form-item><el-form-item v-if="assignmentForm.data_scope_type !== 'GLOBAL'" label="数据范围参数（JSON）" required><el-input v-model="assignmentForm.data_scope_text" type="textarea" :rows="3" /></el-form-item></el-form><template #footer><el-button @click="assignmentVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveAssignment">分配</el-button></template></el-dialog>
  </div>
</template>

<style scoped>
.management-tabs { display: flex; gap: 24px; }.management-tabs button { position: relative; height: 46px; padding: 0; border: 0; color: var(--ink-500); background: transparent; font-size: 15px; cursor: pointer; }.management-tabs button.active { color: var(--ink-900); font-weight: 650; }.management-tabs button.active::after { position: absolute; right: 0; bottom: 0; left: 0; height: 2px; background: var(--accent-500); content: ''; }.list-panel { min-height: 520px; overflow: hidden; }.permission-code { display: inline-block; margin: 2px 5px 2px 0; padding: 3px 6px; border-radius: 4px; background: #f2f5f6; font-size: 11px; }
</style>
