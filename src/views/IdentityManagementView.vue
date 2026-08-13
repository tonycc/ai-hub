<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import ApiState from '../components/ApiState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { apiRequest, queryString } from '../services/platformApi'
import { usePortalSession } from '../stores/session'

const session = usePortalSession()
const activeTab = ref('users')
const loading = ref(false)
const error = ref(null)
const users = ref([])
const organizations = ref([])
const roles = ref([])
const assignments = ref([])
const keyword = ref('')
const userVisible = ref(false)
const orgVisible = ref(false)
const assignmentVisible = ref(false)
const saving = ref(false)
const editingUser = ref(null)
const editingOrg = ref(null)

const userForm = reactive({ subject: '', display_name: '', email: '', organization_id: '', status: 'ACTIVE' })
const orgForm = reactive({ organization_id: '', name: '', parent_organization_id: null, status: 'ACTIVE' })
const assignmentForm = reactive({ user_id: '', role_code: 'APPLICATION_DEVELOPER', application_id: null })
const canWrite = computed(() => session.hasPermission('platform.identity.write'))

async function loadAll() {
  loading.value = true
  error.value = null
  try {
    const suffix = queryString({ query: keyword.value })
    const [userResponse, orgResponse, roleResponse, assignmentResponse] = await Promise.all([
      apiRequest(`users${suffix}`), apiRequest(`organizations${suffix}`),
      apiRequest('platform-roles'), apiRequest('platform-role-assignments'),
    ])
    users.value = userResponse.items
    organizations.value = orgResponse.items
    roles.value = roleResponse.items
    assignments.value = assignmentResponse.items
  } catch (caught) {
    error.value = caught
  } finally {
    loading.value = false
  }
}

function openUser(row = null) {
  editingUser.value = row
  Object.assign(userForm, row ? {
    subject: row.subject, display_name: row.display_name, email: row.email || '',
    organization_id: row.primary_organization_id, status: row.status,
  } : { subject: '', display_name: '', email: '', organization_id: organizations.value[0]?.organization_id || '', status: 'ACTIVE' })
  userVisible.value = true
}

async function saveUser() {
  saving.value = true
  try {
    const path = editingUser.value ? `users/${editingUser.value.user_id}` : 'users'
    const body = { ...userForm, email: userForm.email || null }
    if (editingUser.value) delete body.subject
    await apiRequest(path, { method: editingUser.value ? 'PUT' : 'POST', body })
    userVisible.value = false
    ElMessage.success(editingUser.value ? '用户映射已更新' : '用户映射已创建')
    await loadAll()
  } catch (caught) {
    ElMessage.error(caught.message)
  } finally { saving.value = false }
}

function openOrg(row = null) {
  editingOrg.value = row
  Object.assign(orgForm, row ? {
    organization_id: row.organization_id, name: row.name,
    parent_organization_id: row.parent_organization_id, status: row.status,
  } : { organization_id: '', name: '', parent_organization_id: null, status: 'ACTIVE' })
  orgVisible.value = true
}

async function saveOrg() {
  saving.value = true
  try {
    const path = editingOrg.value ? `organizations/${editingOrg.value.organization_id}` : 'organizations'
    const body = { ...orgForm }
    if (editingOrg.value) delete body.organization_id
    await apiRequest(path, { method: editingOrg.value ? 'PUT' : 'POST', body })
    orgVisible.value = false
    ElMessage.success('组织已保存')
    await loadAll()
  } catch (caught) { ElMessage.error(caught.message) } finally { saving.value = false }
}

async function saveAssignment() {
  saving.value = true
  try {
    await apiRequest('platform-role-assignments', {
      method: 'POST',
      body: { ...assignmentForm, application_id: assignmentForm.application_id || null },
    })
    assignmentVisible.value = false
    ElMessage.success('平台角色已分配')
    await loadAll()
  } catch (caught) { ElMessage.error(caught.message) } finally { saving.value = false }
}

onMounted(loadAll)
</script>

<template>
  <div class="page-shell">
    <PageHeader title="用户与组织" description="管理 authentik 身份映射、组织关系和平台角色分配；认证凭据仍由身份提供方负责。">
      <template #tabs><div class="management-tabs"><button v-for="item in [['users','用户映射'],['organizations','组织'],['roles','平台角色'],['assignments','角色分配']]" :key="item[0]" :class="{ active: activeTab === item[0] }" @click="activeTab = item[0]">{{ item[1] }}</button></div></template>
      <el-input v-model="keyword" clearable prefix-icon="Search" placeholder="搜索名称或编号" style="width: 280px" @keyup.enter="loadAll" @clear="loadAll" />
      <template #actions><el-button @click="loadAll"><el-icon><Refresh /></el-icon>刷新</el-button><el-button v-if="canWrite && activeTab === 'users'" type="primary" @click="openUser()"><el-icon><Plus /></el-icon>新增用户映射</el-button><el-button v-if="canWrite && activeTab === 'organizations'" type="primary" @click="openOrg()"><el-icon><Plus /></el-icon>新增组织</el-button><el-button v-if="canWrite && activeTab === 'assignments'" type="primary" @click="assignmentVisible = true"><el-icon><Plus /></el-icon>分配角色</el-button></template>
    </PageHeader>
    <section class="surface-panel page-section list-panel"><ApiState :loading="loading" :error="error" @retry="loadAll">
      <el-table v-if="activeTab === 'users'" :data="users" style="width: 100%"><el-table-column prop="display_name" label="显示名称" min-width="170" /><el-table-column prop="subject" label="身份主体" min-width="210"><template #default="scope"><code>{{ scope.row.subject }}</code></template></el-table-column><el-table-column prop="email" label="邮箱" min-width="190"><template #default="scope">{{ scope.row.email || '—' }}</template></el-table-column><el-table-column prop="organization_name" label="组织" min-width="150" /><el-table-column label="平台角色" min-width="220"><template #default="scope"><el-tag v-for="role in scope.row.platform_roles" :key="role" size="small" effect="plain" class="inline-tag">{{ role }}</el-tag><span v-if="!scope.row.platform_roles.length">—</span></template></el-table-column><el-table-column prop="authorization_version" label="授权版本" width="100" align="center" /><el-table-column prop="status" label="状态" width="100"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column><el-table-column v-if="canWrite" label="操作" width="80" fixed="right"><template #default="scope"><el-button type="primary" link @click="openUser(scope.row)">编辑</el-button></template></el-table-column></el-table>
      <el-table v-else-if="activeTab === 'organizations'" :data="organizations" style="width: 100%"><el-table-column prop="name" label="组织名称" min-width="220" /><el-table-column prop="organization_id" label="组织编号" min-width="200"><template #default="scope"><code>{{ scope.row.organization_id }}</code></template></el-table-column><el-table-column prop="parent_organization_name" label="上级组织" min-width="180"><template #default="scope">{{ scope.row.parent_organization_name || '—' }}</template></el-table-column><el-table-column prop="user_count" label="用户数" width="100" align="center" /><el-table-column prop="status" label="状态" width="100"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column><el-table-column v-if="canWrite" label="操作" width="80" fixed="right"><template #default="scope"><el-button type="primary" link @click="openOrg(scope.row)">编辑</el-button></template></el-table-column></el-table>
      <el-table v-else-if="activeTab === 'roles'" :data="roles" style="width: 100%"><el-table-column prop="name" label="角色名称" min-width="180" /><el-table-column prop="role_code" label="角色编码" min-width="190"><template #default="scope"><code>{{ scope.row.role_code }}</code></template></el-table-column><el-table-column prop="description" label="职责" min-width="300" /><el-table-column label="权限数" width="100" align="center"><template #default="scope">{{ scope.row.permissions.length }}</template></el-table-column><el-table-column prop="status" label="状态" width="100"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column></el-table>
      <el-table v-else :data="assignments" style="width: 100%"><el-table-column prop="display_name" label="用户" min-width="180" /><el-table-column prop="role_name" label="平台角色" min-width="180" /><el-table-column prop="role_code" label="角色编码" min-width="190"><template #default="scope"><code>{{ scope.row.role_code }}</code></template></el-table-column><el-table-column prop="application_name" label="资源范围" min-width="180"><template #default="scope">{{ scope.row.application_name || '全平台' }}</template></el-table-column><el-table-column prop="created_at" label="分配时间" min-width="190" /></el-table>
    </ApiState></section>

    <el-dialog v-model="userVisible" :title="editingUser ? '编辑用户映射' : '新增用户映射'" width="560px"><el-form label-position="top"><el-form-item v-if="!editingUser" label="OIDC Subject" required><el-input v-model="userForm.subject" /></el-form-item><el-form-item label="显示名称" required><el-input v-model="userForm.display_name" /></el-form-item><el-form-item label="邮箱"><el-input v-model="userForm.email" /></el-form-item><el-form-item label="所属组织" required><el-select v-model="userForm.organization_id" style="width: 100%"><el-option v-for="item in organizations" :key="item.organization_id" :label="item.name" :value="item.organization_id" /></el-select></el-form-item><el-form-item label="状态"><el-switch v-model="userForm.status" active-value="ACTIVE" inactive-value="DISABLED" /></el-form-item></el-form><template #footer><el-button @click="userVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveUser">保存</el-button></template></el-dialog>
    <el-dialog v-model="orgVisible" :title="editingOrg ? '编辑组织' : '新增组织'" width="560px"><el-form label-position="top"><el-form-item v-if="!editingOrg" label="组织编号" required><el-input v-model="orgForm.organization_id" /></el-form-item><el-form-item label="组织名称" required><el-input v-model="orgForm.name" /></el-form-item><el-form-item label="上级组织"><el-select v-model="orgForm.parent_organization_id" clearable style="width: 100%"><el-option v-for="item in organizations.filter((org) => org.organization_id !== editingOrg?.organization_id)" :key="item.organization_id" :label="item.name" :value="item.organization_id" /></el-select></el-form-item><el-form-item label="状态"><el-switch v-model="orgForm.status" active-value="ACTIVE" inactive-value="DISABLED" /></el-form-item></el-form><template #footer><el-button @click="orgVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveOrg">保存</el-button></template></el-dialog>
    <el-dialog v-model="assignmentVisible" title="分配平台角色" width="560px"><el-form label-position="top"><el-form-item label="用户" required><el-select v-model="assignmentForm.user_id" filterable style="width: 100%"><el-option v-for="item in users" :key="item.user_id" :label="`${item.display_name} · ${item.subject}`" :value="item.user_id" /></el-select></el-form-item><el-form-item label="角色" required><el-select v-model="assignmentForm.role_code" style="width: 100%"><el-option v-for="item in roles" :key="item.role_code" :label="item.name" :value="item.role_code" /></el-select></el-form-item><el-form-item label="应用编号"><el-input v-model="assignmentForm.application_id" placeholder="仅应用开发者需要；全局角色留空" /></el-form-item></el-form><template #footer><el-button @click="assignmentVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveAssignment">分配</el-button></template></el-dialog>
  </div>
</template>

<style scoped>
.management-tabs { display: flex; gap: 24px; }
.management-tabs button { position: relative; height: 46px; padding: 0 1px; border: 0; color: var(--ink-500); background: transparent; font-size: 15px; cursor: pointer; }
.management-tabs button.active { color: var(--ink-900); font-weight: 650; }
.management-tabs button.active::after { position: absolute; right: 0; bottom: 0; left: 0; height: 2px; background: var(--accent-500); content: ''; }
.list-panel { min-height: 520px; overflow: hidden; }
.inline-tag { margin: 2px 4px 2px 0; }
</style>
