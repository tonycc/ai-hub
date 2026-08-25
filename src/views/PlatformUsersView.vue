<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import ApiState from '../components/ApiState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { apiRequest, queryString } from '../services/platformApi'
import { usePortalSession } from '../stores/session'

const session = usePortalSession()
const activeTab = ref('platform-users')
const loading = ref(false)
const error = ref(null)
const users = ref([])
const organizations = ref([])
const roles = ref([])
const assignments = ref([])
const keyword = ref('')
const userVisible = ref(false)
const authentikUserVisible = ref(false)
const authentikPasswordVisible = ref(false)
const authentikUsers = ref([])
const loadingAuthentikUsers = ref(false)
const unifiedUserVisible = ref(false)
const unifiedPasswordVisible = ref(false)
const unifiedUsers = ref([])
const loadingUnifiedUsers = ref(false)

const platformRoleMap = {
  PLATFORM_ADMIN: '平台管理员',
  APPLICATION_DEVELOPER: '应用开发者',
}
const assignmentVisible = ref(false)
const saving = ref(false)
const editingUser = ref(null)
const editingAuthentikUser = ref(null)
const editingUnifiedUser = ref(null)

const userForm = reactive({ subject: '', display_name: '', email: '', organization_id: '', status: 'ACTIVE' })
const assignmentForm = reactive({ user_id: '', role_code: 'APPLICATION_DEVELOPER', application_id: null })
const authentikUserForm = reactive({ username: '', name: '', email: '', password: '' })
const authentikPasswordForm = reactive({ password: '' })
const unifiedUserForm = reactive({ login_account: '', user_name: '', password: '', organization_id: '', role_code: '', application_id: null, status: 'ACTIVE' })
const unifiedPasswordForm = reactive({ password: '' })
const canWrite = computed(() => session.hasPermission('platform.identity.write'))

// 平台用户 = 有平台角色的用户
const platformUsers = computed(() => unifiedUsers.value.filter((user) => user.platform_roles.length > 0))
// Unfiltered ACTIVE options for the create/edit forms: the list tabs follow
// the search keyword, but the form selectors must keep offering every active
// organization and role while a list search is in progress.
const formOrganizations = ref([])
const formRoles = ref([])
const formUsers = ref([])
const activeFormRoles = computed(() => formRoles.value.filter((role) => role.status === 'ACTIVE'))

async function loadFormOptions() {
  try {
    const [orgResponse, roleResponse, userResponse] = await Promise.all([
      apiRequest('organizations?status=ACTIVE'),
      apiRequest('platform-roles'),
      apiRequest('users?status=ACTIVE'),
    ])
    formOrganizations.value = orgResponse.items
    formRoles.value = roleResponse.items
    formUsers.value = userResponse.items
  } catch {
    formOrganizations.value = []
    formRoles.value = []
    formUsers.value = []
  }
}

async function loadAll() {
  loading.value = true
  error.value = null
  try {
    const suffix = queryString({ query: keyword.value })
    const [userResponse, orgResponse, roleResponse, assignmentResponse] = await Promise.all([
      apiRequest(`users${suffix}`), apiRequest(`organizations${suffix}`),
      apiRequest(`platform-roles${suffix}`), apiRequest(`platform-role-assignments${suffix}`),
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

async function loadUnifiedUsers() {
  loadingUnifiedUsers.value = true
  try {
    const response = await apiRequest(`unified-users${queryString({ query: keyword.value })}`)
    unifiedUsers.value = response || []
  } catch (caught) {
    ElMessage.error('加载用户失败: ' + caught.message)
  } finally {
    loadingUnifiedUsers.value = false
  }
}

async function loadAuthentikUsers() {
  loadingAuthentikUsers.value = true
  try {
    const response = await apiRequest(`authentik-users${queryString({ query: keyword.value })}`)
    authentikUsers.value = response.items || []
  } catch (caught) {
    ElMessage.error('加载 Authentik 用户失败: ' + caught.message)
  } finally {
    loadingAuthentikUsers.value = false
  }
}

// 搜索和刷新需要根据当前标签页更新对应的数据源
function refreshCurrent() {
  if (activeTab.value === 'platform-users') {
    loadUnifiedUsers()
  } else if (activeTab.value === 'authentik-users') {
    loadAuthentikUsers()
  } else {
    loadAll()
  }
}

function openUser(row = null) {
  editingUser.value = row
  Object.assign(userForm, row ? {
    subject: row.subject, display_name: row.display_name, email: row.email || '',
    organization_id: row.primary_organization_id, status: row.status,
  } : { subject: '', display_name: '', email: '', organization_id: formOrganizations.value[0]?.organization_id || '', status: 'ACTIVE' })
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
    await loadUnifiedUsers()
  } catch (caught) { ElMessage.error(caught.message) } finally { saving.value = false }
}

function openAuthentikUser(row = null) {
  editingAuthentikUser.value = row
  Object.assign(authentikUserForm, row ? {
    username: row.username, name: row.name, email: row.email || '', password: '',
  } : { username: '', name: '', email: '', password: '' })
  authentikUserVisible.value = true
}

async function saveAuthentikUser() {
  saving.value = true
  try {
    const path = editingAuthentikUser.value
      ? `authentik-users/${editingAuthentikUser.value.username}`
      : 'authentik-users'
    const body = { ...authentikUserForm }
    if (editingAuthentikUser.value) delete body.username
    if (!body.password) delete body.password
    await apiRequest(path, { method: editingAuthentikUser.value ? 'PATCH' : 'POST', body })
    authentikUserVisible.value = false
    ElMessage.success(editingAuthentikUser.value ? 'Authentik 用户已更新' : 'Authentik 用户已创建')
    await loadAuthentikUsers()
  } catch (caught) { ElMessage.error(caught.message) } finally { saving.value = false }
}

function openAuthentikPassword(row) {
  editingAuthentikUser.value = row
  authentikPasswordForm.password = ''
  authentikPasswordVisible.value = true
}

async function saveAuthentikPassword() {
  saving.value = true
  try {
    await apiRequest(`authentik-users/${editingAuthentikUser.value.username}/set-password`, {
      method: 'POST',
      body: { password: authentikPasswordForm.password },
    })
    authentikPasswordVisible.value = false
    ElMessage.success('密码已重置')
  } catch (caught) { ElMessage.error(caught.message) } finally { saving.value = false }
}

async function deleteAuthentikUser(row) {
  try {
    await ElMessageBox.confirm(
      `删除 Authentik 用户 "${row.username}" 后，该用户将无法登录。此操作不可恢复。`,
      '确认删除',
      { confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  try {
    await apiRequest(`authentik-users/${row.username}`, { method: 'DELETE' })
    ElMessage.success('Authentik 用户已删除')
    await loadAuthentikUsers()
  } catch (caught) { ElMessage.error(caught.message) }
}

function openUnifiedUser(row = null) {
  editingUnifiedUser.value = row
  Object.assign(unifiedUserForm, row ? {
    login_account: row.login_account, user_name: row.user_name, password: '',
    organization_id: row.organization_id, role_code: row.platform_roles[0] || '', application_id: null,
    status: row.status || 'ACTIVE',
  } : { login_account: '', user_name: '', password: '', organization_id: formOrganizations.value[0]?.organization_id || '', role_code: '', application_id: null, status: 'ACTIVE' })
  unifiedUserVisible.value = true
}

async function saveUnifiedUser() {
  // 平台用户必须至少有一个平台角色；应用开发者必须绑定应用。
  if (!unifiedUserForm.role_code && !editingUnifiedUser.value) {
    ElMessage.warning('请选择平台角色')
    return
  }
  if (unifiedUserForm.role_code === 'APPLICATION_DEVELOPER' && !unifiedUserForm.application_id) {
    ElMessage.warning('应用开发者必须绑定应用编号')
    return
  }
  saving.value = true
  try {
    if (editingUnifiedUser.value) {
      // 编辑路径走统一更新端点：姓名/组织/状态在 Authentik 与本地同时生效。
      const body = {
        user_name: unifiedUserForm.user_name,
        organization_id: unifiedUserForm.organization_id,
        status: unifiedUserForm.status,
      }
      await apiRequest(`unified-users/${editingUnifiedUser.value.user_id}`, { method: 'PUT', body })
      ElMessage.success('平台用户已更新')
    } else {
      const body = { ...unifiedUserForm }
      if (!body.application_id) delete body.application_id
      delete body.status
      await apiRequest('unified-users', { method: 'POST', body })
      ElMessage.success('用户已创建')
    }
    unifiedUserVisible.value = false
    await loadUnifiedUsers()
    await loadAll()
  } catch (caught) { ElMessage.error(caught.message) } finally { saving.value = false }
}

async function toggleUnifiedUserStatus(row) {
  const target = row.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE'
  const action = target === 'ACTIVE' ? '启用' : '停用'
  try {
    await ElMessageBox.confirm(`确定${action}平台用户「${row.user_name}」吗？`, `${action}用户`, { type: 'warning' })
  } catch { return }
  try {
    await apiRequest(`unified-users/${row.user_id}`, { method: 'PUT', body: { status: target } })
    ElMessage.success(`已${action}`)
    await loadUnifiedUsers()
    await loadAll()
  } catch (caught) { ElMessage.error(caught.message) }
}

function openUnifiedPassword(row) {
  editingUnifiedUser.value = row
  unifiedPasswordForm.password = ''
  unifiedPasswordVisible.value = true
}

async function saveUnifiedPassword() {
  saving.value = true
  try {
    await apiRequest(`unified-users/${editingUnifiedUser.value.user_id}`, {
      method: 'PUT',
      body: { password: unifiedPasswordForm.password },
    })
    unifiedPasswordVisible.value = false
    ElMessage.success('密码已重置')
  } catch (caught) { ElMessage.error(caught.message) } finally { saving.value = false }
}


onMounted(() => {
  loadAll()
  loadUnifiedUsers()
  loadAuthentikUsers()
  loadFormOptions()
})
</script>

<template>
  <div class="page-shell">
    <PageHeader title="平台用户" description="管理平台管理员、应用开发者等平台级用户及其角色分配。">
      <template #tabs>
        <div class="management-tabs">
          <button v-for="item in [['platform-users','平台用户'],['roles','平台角色'],['assignments','角色分配'],['authentik-users','Authentik 用户']]" :key="item[0]" :class="{ active: activeTab === item[0] }" @click="activeTab = item[0]">{{ item[1] }}</button>
        </div>
      </template>
      <el-input v-model="keyword" clearable prefix-icon="Search" placeholder="搜索名称或编号" style="width: 280px" @keyup.enter="refreshCurrent" @clear="refreshCurrent" />
      <template #actions>
        <el-button @click="refreshCurrent"><el-icon><Refresh /></el-icon>刷新</el-button>
        <el-button v-if="canWrite && activeTab === 'platform-users'" type="primary" @click="openUnifiedUser()"><el-icon><Plus /></el-icon>新增平台用户</el-button>
        <el-button v-if="canWrite && activeTab === 'assignments'" type="primary" @click="assignmentVisible = true"><el-icon><Plus /></el-icon>分配角色</el-button>
        <el-button v-if="canWrite && activeTab === 'authentik-users'" type="primary" @click="openAuthentikUser()"><el-icon><Plus /></el-icon>新增 Authentik 用户</el-button>
      </template>
    </PageHeader>
    <section class="surface-panel page-section list-panel">
      <ApiState :loading="loading" :error="error" @retry="loadAll">
        <template v-if="activeTab === 'platform-users'">
          <ApiState :loading="loadingUnifiedUsers" :error="null" @retry="loadUnifiedUsers">
            <el-table :data="platformUsers" style="width: 100%">
              <el-table-column prop="user_name" label="用户姓名" min-width="150" />
              <el-table-column prop="login_account" label="登录账号" min-width="180">
                <template #default="scope"><code>{{ scope.row.login_account }}</code></template>
              </el-table-column>
              <el-table-column prop="email" label="邮箱" min-width="180">
                <template #default="scope">{{ scope.row.email || '—' }}</template>
              </el-table-column>
              <el-table-column prop="organization_name" label="组织" min-width="140" />
              <el-table-column label="平台角色" min-width="180">
                <template #default="scope">
                  <el-tag v-for="role in scope.row.platform_roles" :key="role" size="small" effect="plain" class="inline-tag">{{ platformRoleMap[role] || role }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="status" label="状态" width="90">
                <template #default="scope"><StatusTag :status="scope.row.status" /></template>
              </el-table-column>
              <el-table-column v-if="canWrite" label="操作" width="260" fixed="right">
                <template #default="scope">
                  <el-button type="primary" link @click="openUnifiedUser(scope.row)">编辑</el-button>
                  <el-button type="primary" link @click="openUnifiedPassword(scope.row)">重置密码</el-button>
                  <el-button :type="scope.row.status === 'ACTIVE' ? 'danger' : 'success'" link @click="toggleUnifiedUserStatus(scope.row)">{{ scope.row.status === 'ACTIVE' ? '停用' : '启用' }}</el-button>
                </template>
              </el-table-column>
            </el-table>
          </ApiState>
        </template>
        <el-table v-else-if="activeTab === 'roles'" :data="roles" style="width: 100%">
          <el-table-column prop="name" label="角色名称" min-width="180" />
          <el-table-column prop="role_code" label="角色编码" min-width="190">
            <template #default="scope"><code>{{ scope.row.role_code }}</code></template>
          </el-table-column>
          <el-table-column prop="description" label="职责" min-width="300" />
          <el-table-column label="权限数" width="100" align="center">
            <template #default="scope">{{ scope.row.permissions.length }}</template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="100">
            <template #default="scope"><StatusTag :status="scope.row.status" /></template>
          </el-table-column>
        </el-table>
        <template v-else-if="activeTab === 'authentik-users'">
          <ApiState :loading="loadingAuthentikUsers" :error="null" @retry="loadAuthentikUsers">
            <el-table :data="authentikUsers" style="width: 100%">
              <el-table-column prop="username" label="用户名" min-width="180" />
              <el-table-column prop="name" label="显示名称" min-width="180" />
              <el-table-column prop="email" label="邮箱" min-width="200">
                <template #default="scope">{{ scope.row.email || '—' }}</template>
              </el-table-column>
              <el-table-column prop="is_active" label="状态" width="100">
                <template #default="scope">
                  <el-tag :type="scope.row.is_active ? 'success' : 'info'" size="small">{{ scope.row.is_active ? '启用' : '禁用' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column v-if="canWrite" label="操作" width="200" fixed="right">
                <template #default="scope">
                  <el-button type="primary" link @click="openAuthentikUser(scope.row)">编辑</el-button>
                  <el-button type="primary" link @click="openAuthentikPassword(scope.row)">重置密码</el-button>
                  <el-button type="danger" link @click="deleteAuthentikUser(scope.row)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
          </ApiState>
        </template>
        <el-table v-else :data="assignments" style="width: 100%">
          <el-table-column prop="display_name" label="用户" min-width="180" />
          <el-table-column prop="role_name" label="平台角色" min-width="180" />
          <el-table-column prop="role_code" label="角色编码" min-width="190">
            <template #default="scope"><code>{{ scope.row.role_code }}</code></template>
          </el-table-column>
          <el-table-column prop="application_name" label="资源范围" min-width="180">
            <template #default="scope">{{ scope.row.application_name || '全平台' }}</template>
          </el-table-column>
          <el-table-column prop="created_at" label="分配时间" min-width="190" />
        </el-table>
      </ApiState>
    </section>

    <el-drawer v-model="userVisible" :title="editingUser ? '编辑用户映射' : '新增用户映射'" size="min(560px, 96vw)">
      <el-form label-position="top">
        <el-form-item v-if="!editingUser" label="OIDC Subject" required><el-input v-model="userForm.subject" /></el-form-item>
        <el-form-item label="显示名称" required><el-input v-model="userForm.display_name" /></el-form-item>
        <el-form-item label="邮箱"><el-input v-model="userForm.email" /></el-form-item>
        <el-form-item label="所属组织" required>
          <el-select v-model="userForm.organization_id" style="width: 100%">
            <el-option v-for="item in formOrganizations" :key="item.organization_id" :label="item.name" :value="item.organization_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态"><el-switch v-model="userForm.status" active-value="ACTIVE" inactive-value="DISABLED" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="userVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveUser">保存</el-button>
      </template>
    </el-drawer>

    <el-drawer v-model="assignmentVisible" title="分配平台角色" size="min(560px, 96vw)">
      <el-form label-position="top">
        <el-form-item label="用户" required>
          <el-select v-model="assignmentForm.user_id" filterable style="width: 100%">
            <el-option v-for="item in formUsers" :key="item.user_id" :label="`${item.display_name} · ${item.subject}`" :value="item.user_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="角色" required>
          <el-select v-model="assignmentForm.role_code" style="width: 100%">
            <el-option v-for="item in activeFormRoles" :key="item.role_code" :label="item.name" :value="item.role_code" />
          </el-select>
        </el-form-item>
        <el-form-item label="应用编号"><el-input v-model="assignmentForm.application_id" placeholder="仅应用开发者需要；全局角色留空" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="assignmentVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveAssignment">分配</el-button>
      </template>
    </el-drawer>

    <el-drawer v-model="authentikUserVisible" :title="editingAuthentikUser ? '编辑 Authentik 用户' : '新增 Authentik 用户'" size="min(560px, 96vw)">
      <el-form label-position="top">
        <el-form-item v-if="!editingAuthentikUser" label="用户名" required><el-input v-model="authentikUserForm.username" placeholder="登录用户名" /></el-form-item>
        <el-form-item label="显示名称" required><el-input v-model="authentikUserForm.name" /></el-form-item>
        <el-form-item label="邮箱"><el-input v-model="authentikUserForm.email" /></el-form-item>
        <el-form-item v-if="!editingAuthentikUser" label="初始密码" required><el-input v-model="authentikUserForm.password" type="password" show-password placeholder="至少8位" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="authentikUserVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveAuthentikUser">保存</el-button>
      </template>
    </el-drawer>

    <el-drawer v-model="authentikPasswordVisible" title="重置密码" size="min(480px, 96vw)">
      <el-form label-position="top">
        <el-form-item label="新密码" required><el-input v-model="authentikPasswordForm.password" type="password" show-password placeholder="至少8位" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="authentikPasswordVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveAuthentikPassword">重置</el-button>
      </template>
    </el-drawer>

    <el-drawer v-model="unifiedUserVisible" :title="editingUnifiedUser ? '编辑平台用户' : '新增平台用户'" size="min(560px, 96vw)">
      <el-form label-position="top">
        <el-form-item label="登录账号" required>
          <el-input v-model="unifiedUserForm.login_account" placeholder="手机号或邮箱" :disabled="Boolean(editingUnifiedUser)" />
        </el-form-item>
        <el-form-item label="用户姓名" required>
          <el-input v-model="unifiedUserForm.user_name" placeholder="界面显示名称" />
        </el-form-item>
        <el-form-item v-if="!editingUnifiedUser" label="初始密码" required>
          <el-input v-model="unifiedUserForm.password" type="password" show-password placeholder="至少8位" />
        </el-form-item>
        <el-form-item label="所属组织" required>
          <el-select v-model="unifiedUserForm.organization_id" style="width: 100%">
            <el-option v-for="item in formOrganizations" :key="item.organization_id" :label="item.name" :value="item.organization_id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="!editingUnifiedUser" label="平台角色" required>
          <el-select v-model="unifiedUserForm.role_code" style="width: 100%">
            <el-option v-for="item in activeFormRoles" :key="item.role_code" :label="item.name" :value="item.role_code" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="!editingUnifiedUser && unifiedUserForm.role_code === 'APPLICATION_DEVELOPER'" label="应用编号">
          <el-input v-model="unifiedUserForm.application_id" placeholder="应用开发者需指定应用" />
        </el-form-item>
        <el-form-item v-if="editingUnifiedUser" label="状态">
          <el-radio-group v-model="unifiedUserForm.status">
            <el-radio-button value="ACTIVE">启用</el-radio-button>
            <el-radio-button value="DISABLED">停用</el-radio-button>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="unifiedUserVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveUnifiedUser">{{ editingUnifiedUser ? '保存' : '创建' }}</el-button>
      </template>
    </el-drawer>

    <el-drawer v-model="unifiedPasswordVisible" title="重置密码" size="min(480px, 96vw)">
      <el-form label-position="top">
        <el-form-item label="新密码" required>
          <el-input v-model="unifiedPasswordForm.password" type="password" show-password placeholder="至少8位" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="unifiedPasswordVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveUnifiedPassword">重置</el-button>
      </template>
    </el-drawer>
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
