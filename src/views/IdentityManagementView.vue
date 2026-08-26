<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import ApiState from '../components/ApiState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { apiRequest, formatApiError, queryString } from '../services/platformApi'
import { usePortalSession } from '../stores/session'

const session = usePortalSession()
const activeTab = ref('users')
const loading = ref(false)
const error = ref(null)
const users = ref([])
const organizations = ref([])
const positions = ref([])
// Unfiltered position options for the user form / assignment selectors: the
// management list follows the search keyword, but the selectors must keep
// offering every active position while a user search is in progress.
const allPositions = ref([])
const allOrganizations = ref([])
const keyword = ref('')
const userVisible = ref(false)
const orgVisible = ref(false)
const positionVisible = ref(false)
const userPositionVisible = ref(false)
const passwordVisible = ref(false)
const saving = ref(false)
const editingUser = ref(null)
const passwordUser = ref(null)
const editingOrg = ref(null)
const editingPosition = ref(null)
const selectedUser = ref(null)

const userForm = reactive({
  login_account: '',
  user_name: '',
  password: '',
  email: '',
  organization_id: '',
  position_code: '',
  status: 'ACTIVE'
})
const orgForm = reactive({ organization_id: '', name: '', parent_organization_id: null, status: 'ACTIVE' })
const positionForm = reactive({ position_code: '', name: '', description: '' })
const userPositionForm = reactive({ organization_id: '', position_code: '', is_primary: false })
const passwordForm = reactive({ password: '' })
const canWrite = computed(() => session.hasPermission('platform.identity.write'))

const PHONE_PATTERN = /^1[3-9]\d{9}$/
const EMAIL_PATTERN = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/

function validateUserForm() {
  if (!userForm.user_name?.trim()) return '请填写用户姓名'
  if (!userForm.organization_id) return '请选择所属组织'
  if (!editingUser.value) {
    if (!PHONE_PATTERN.test(userForm.login_account) && !EMAIL_PATTERN.test(userForm.login_account)) {
      return '登录账号必须是手机号或邮箱格式'
    }
    if (!userForm.password || userForm.password.length < 8) return '初始密码至少需要 8 位'
  }
  return null
}

// 业务用户 = 无平台角色的用户
const businessUsers = computed(() => users.value.filter((user) => user.platform_roles.length === 0))

// 用户职位列表
const userPositions = ref([])

async function loadAll() {
  loading.value = true
  error.value = null
  try {
    const suffix = queryString({ query: keyword.value })
    const [userResponse, orgResponse, positionResponse, allPositionResponse, allOrgResponse] = await Promise.all([
      apiRequest(`users${suffix}`),
      apiRequest(`organizations${suffix}`),
      apiRequest(`positions${suffix}`),
      apiRequest('positions?status=ACTIVE'),
      apiRequest('organizations?status=ACTIVE'),
    ])
    users.value = userResponse.items
    organizations.value = orgResponse.items
    positions.value = positionResponse.items
    allPositions.value = allPositionResponse.items
    allOrganizations.value = allOrgResponse.items
  } catch (caught) {
    error.value = caught
  } finally {
    loading.value = false
  }
}

async function loadUserPositions(userId) {
  try {
    userPositions.value = await apiRequest(`users/${userId}/positions`)
  } catch (caught) {
    ElMessage.error('加载用户职位失败: ' + caught.message)
  }
}

function openUser(row = null) {
  editingUser.value = row
  selectedUser.value = row
  if (row) {
    // 编辑模式：加载用户职位
    loadUserPositions(row.user_id)
    const primaryPosition = row.positions?.find(p => p.is_primary) || row.positions?.[0]
    Object.assign(userForm, {
      login_account: row.subject,
      user_name: row.display_name,
      password: '',
      email: row.email || '',
      organization_id: row.primary_organization_id,
      position_code: primaryPosition?.position_code || '',
      status: row.status,
    })
  } else {
    Object.assign(userForm, {
      login_account: '',
      user_name: '',
      password: '',
      email: '',
      organization_id: allOrganizations.value[0]?.organization_id || '',
      position_code: '',
      status: 'ACTIVE'
    })
  }
  userVisible.value = true
}

async function saveUser() {
  const validationError = validateUserForm()
  if (validationError) {
    ElMessage.warning(validationError)
    return
  }
  saving.value = true
  try {
    if (editingUser.value) {
      const body = {
        user_name: userForm.user_name,
        email: userForm.email || null,
        organization_id: userForm.organization_id,
        status: userForm.status,
        is_active: userForm.status === 'ACTIVE',
      }
      await apiRequest(`unified-users/${editingUser.value.user_id}`, { method: 'PUT', body })
      // 更新职位：职位或组织变化时写入；显式清空时删除原主职位，否则页面
      // 提示成功但旧职位仍保留。
      const currentPrimary = editingUser.value.positions?.find(p => p.is_primary) || editingUser.value.positions?.[0]
      const positionChanged = userForm.position_code && userForm.position_code !== currentPrimary?.position_code
      const orgChanged = userForm.organization_id !== currentPrimary?.organization_id
      const positionCleared = !userForm.position_code && Boolean(currentPrimary)
      if (positionChanged || (userForm.position_code && orgChanged)) {
        await apiRequest(`users/${editingUser.value.user_id}/positions`, {
          method: 'POST',
          body: {
            organization_id: userForm.organization_id,
            position_code: userForm.position_code,
            is_primary: true
          }
        })
      } else if (positionCleared) {
        await apiRequest(
          `users/${editingUser.value.user_id}/positions/${currentPrimary.assignment_id}`,
          { method: 'DELETE' }
        )
      }
      ElMessage.success('业务用户已更新')
    } else {
      const body = {
        login_account: userForm.login_account,
        user_name: userForm.user_name,
        password: userForm.password,
        organization_id: userForm.organization_id,
        position_code: userForm.position_code || undefined
      }
      if (userForm.email) body.email = userForm.email
      await apiRequest('unified-users', { method: 'POST', body })
      ElMessage.success('业务用户已创建')
    }
    userVisible.value = false
    await loadAll()
  } catch (caught) {
    ElMessage.error(formatApiError(caught))
  } finally { saving.value = false }
}

function openPassword(row) {
  passwordUser.value = row
  passwordForm.password = ''
  passwordVisible.value = true
}

async function savePassword() {
  if (!passwordForm.password || passwordForm.password.length < 8) {
    ElMessage.warning('新密码至少需要 8 位')
    return
  }
  saving.value = true
  try {
    await apiRequest(`unified-users/${passwordUser.value.user_id}`, {
      method: 'PUT',
      body: { password: passwordForm.password },
    })
    passwordVisible.value = false
    ElMessage.success('密码已重置')
  } catch (caught) {
    ElMessage.error(formatApiError(caught))
  } finally {
    saving.value = false
  }
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

// 职位管理
function openPosition(row = null) {
  editingPosition.value = row
  Object.assign(positionForm, row ? {
    position_code: row.position_code,
    name: row.name,
    description: row.description || '',
  } : { position_code: '', name: '', description: '' })
  positionVisible.value = true
}

async function savePosition() {
  saving.value = true
  try {
    const path = editingPosition.value ? `positions/${editingPosition.value.position_code}` : 'positions'
    const body = { ...positionForm }
    if (editingPosition.value) delete body.position_code
    await apiRequest(path, { method: editingPosition.value ? 'PUT' : 'POST', body })
    positionVisible.value = false
    ElMessage.success('职位已保存')
    await loadAll()
  } catch (caught) { ElMessage.error(caught.message) } finally { saving.value = false }
}

async function deletePosition(row) {
  try {
    await ElMessageBox.confirm(`确定删除职位 "${row.name}" 吗？`, '确认删除', { type: 'warning' })
  } catch { return }
  try {
    await apiRequest(`positions/${row.position_code}`, { method: 'DELETE' })
    ElMessage.success('职位已删除')
    await loadAll()
  } catch (caught) { ElMessage.error(caught.message) }
}

// 用户职位管理
function openUserPosition(row) {
  selectedUser.value = row
  userPositionForm.organization_id = row.primary_organization_id
  userPositionForm.position_code = ''
  userPositionForm.is_primary = false
  userPositionVisible.value = true
}

async function saveUserPosition() {
  saving.value = true
  try {
    await apiRequest(`users/${selectedUser.value.user_id}/positions`, {
      method: 'POST',
      body: { ...userPositionForm }
    })
    userPositionVisible.value = false
    ElMessage.success('职位分配成功')
    await loadAll()
    await loadUserPositions(selectedUser.value.user_id)
  } catch (caught) { ElMessage.error(caught.message) } finally { saving.value = false }
}

async function removeUserPosition(position) {
  const user = selectedUser.value
  try {
    await ElMessageBox.confirm(
      `确定将用户「${user?.display_name}」在组织「${position.organization_name || position.organization_id}」的职位「${position.position_name || position.position_code}」移除吗？`,
      '确认移除职位',
      { type: 'warning', confirmButtonText: '移除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await apiRequest(`users/${user.user_id}/positions/${position.assignment_id}`, { method: 'DELETE' })
    ElMessage.success('职位已移除')
    await loadAll()
    await loadUserPositions(user.user_id)
  } catch (caught) { ElMessage.error(caught.message) }
}

onMounted(loadAll)
</script>

<template>
  <div class="page-shell">
    <PageHeader title="用户与组织" description="管理业务用户和组织架构；业务用户仅用于登录业务应用，无平台管理权限。">
      <template #tabs>
        <div class="management-tabs">
          <button v-for="item in [['users','业务用户'],['organizations','组织'],['positions','职位']]" :key="item[0]" :class="{ active: activeTab === item[0] }" @click="activeTab = item[0]">{{ item[1] }}</button>
        </div>
      </template>
      <el-input v-model="keyword" clearable prefix-icon="Search" placeholder="搜索名称或编号" style="width: 280px" @keyup.enter="loadAll" @clear="loadAll" />
      <template #actions>
        <el-button @click="loadAll"><el-icon><Refresh /></el-icon>刷新</el-button>
        <el-button v-if="canWrite && activeTab === 'users'" type="primary" @click="openUser()"><el-icon><Plus /></el-icon>新增业务用户</el-button>
        <el-button v-if="canWrite && activeTab === 'organizations'" type="primary" @click="openOrg()"><el-icon><Plus /></el-icon>新增组织</el-button>
        <el-button v-if="canWrite && activeTab === 'positions'" type="primary" @click="openPosition()"><el-icon><Plus /></el-icon>新增职位</el-button>
      </template>
    </PageHeader>

    <section class="surface-panel page-section list-panel">
      <ApiState :loading="loading" :error="error" @retry="loadAll">
        <!-- 业务用户表格 -->
        <el-table v-if="activeTab === 'users'" :data="businessUsers" style="width: 100%">
          <el-table-column prop="display_name" label="用户姓名" min-width="150" />
          <el-table-column prop="subject" label="登录账号" min-width="180">
            <template #default="scope"><code>{{ scope.row.subject }}</code></template>
          </el-table-column>
          <el-table-column prop="email" label="邮箱" min-width="180">
            <template #default="scope">{{ scope.row.email || '—' }}</template>
          </el-table-column>
          <el-table-column label="组织/职位" min-width="200">
            <template #default="scope">
              <div v-if="scope.row.positions?.length">
                <el-tag v-for="pos in scope.row.positions" :key="pos.assignment_id" size="small" effect="plain" class="inline-tag">
                  {{ pos.organization_name }} · {{ pos.position_name }}
                  <el-icon v-if="pos.is_primary" class="primary-icon"><Star /></el-icon>
                </el-tag>
              </div>
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="90">
            <template #default="scope"><StatusTag :status="scope.row.status" /></template>
          </el-table-column>
          <el-table-column v-if="canWrite" label="操作" width="220" fixed="right">
            <template #default="scope">
              <el-button type="primary" link @click="openUser(scope.row)">编辑</el-button>
              <el-button type="primary" link @click="openPassword(scope.row)">重置密码</el-button>
              <el-button type="primary" link @click="openUserPosition(scope.row)">分配职位</el-button>
            </template>
          </el-table-column>
        </el-table>

        <!-- 组织表格 -->
        <el-table v-else-if="activeTab === 'organizations'" :data="organizations" style="width: 100%">
          <el-table-column prop="name" label="组织名称" min-width="220" />
          <el-table-column prop="organization_id" label="组织编号" min-width="200">
            <template #default="scope"><code>{{ scope.row.organization_id }}</code></template>
          </el-table-column>
          <el-table-column prop="parent_organization_name" label="上级组织" min-width="180">
            <template #default="scope">{{ scope.row.parent_organization_name || '—' }}</template>
          </el-table-column>
          <el-table-column prop="user_count" label="用户数" width="100" align="center" />
          <el-table-column prop="status" label="状态" width="100">
            <template #default="scope"><StatusTag :status="scope.row.status" /></template>
          </el-table-column>
          <el-table-column v-if="canWrite" label="操作" width="80" fixed="right">
            <template #default="scope">
              <el-button type="primary" link @click="openOrg(scope.row)">编辑</el-button>
            </template>
          </el-table-column>
        </el-table>

        <!-- 职位表格 -->
        <el-table v-else-if="activeTab === 'positions'" :data="positions" style="width: 100%">
          <el-table-column prop="name" label="职位名称" min-width="180" />
          <el-table-column prop="position_code" label="职位编码" min-width="150">
            <template #default="scope"><code>{{ scope.row.position_code }}</code></template>
          </el-table-column>
          <el-table-column prop="description" label="描述" min-width="200">
            <template #default="scope">{{ scope.row.description || '—' }}</template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="100">
            <template #default="scope"><StatusTag :status="scope.row.status" /></template>
          </el-table-column>
          <el-table-column v-if="canWrite" label="操作" width="150" fixed="right">
            <template #default="scope">
              <el-button type="primary" link @click="openPosition(scope.row)">编辑</el-button>
              <el-button type="danger" link @click="deletePosition(scope.row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </ApiState>
    </section>

    <!-- 新增/编辑业务用户 -->
    <el-drawer v-model="userVisible" :title="editingUser ? '编辑业务用户' : '新增业务用户'" size="min(560px, 96vw)">
      <el-form label-position="top">
        <el-form-item v-if="!editingUser" label="登录账号" required>
          <el-input v-model="userForm.login_account" placeholder="手机号或邮箱" />
        </el-form-item>
        <el-form-item label="用户姓名" required>
          <el-input v-model="userForm.user_name" placeholder="界面显示名称" />
        </el-form-item>
        <el-form-item v-if="!editingUser" label="初始密码" required>
          <el-input v-model="userForm.password" type="password" show-password placeholder="至少8位" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="userForm.email" />
        </el-form-item>
        <el-form-item label="所属组织" required>
          <el-select v-model="userForm.organization_id" style="width: 100%">
            <el-option v-for="item in allOrganizations" :key="item.organization_id" :label="item.name" :value="item.organization_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="职位">
          <el-select v-model="userForm.position_code" clearable style="width: 100%" placeholder="选择职位">
            <el-option v-for="item in allPositions" :key="item.position_code" :label="item.name" :value="item.position_code" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="editingUser" label="状态">
          <el-switch v-model="userForm.status" active-value="ACTIVE" inactive-value="DISABLED" />
        </el-form-item>

        <!-- 编辑模式下显示已分配职位 -->
        <template v-if="editingUser && userPositions.length">
          <el-divider>已分配职位</el-divider>
          <div class="position-list">
            <div v-for="pos in userPositions" :key="pos.assignment_id" class="position-item">
              <span>{{ pos.organization_name }} · {{ pos.position_name }}</span>
              <el-tag v-if="pos.is_primary" size="small" type="warning">主职位</el-tag>
              <el-button type="danger" link size="small" @click="removeUserPosition(pos)">移除</el-button>
            </div>
          </div>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="userVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveUser">保存</el-button>
      </template>
    </el-drawer>

    <el-drawer v-model="passwordVisible" title="重置密码" size="min(480px, 96vw)">
      <el-form label-position="top">
        <el-form-item label="用户">
          <el-input :model-value="passwordUser?.display_name" disabled />
        </el-form-item>
        <el-form-item label="登录账号">
          <el-input :model-value="passwordUser?.subject" disabled />
        </el-form-item>
        <el-form-item label="新密码" required>
          <el-input v-model="passwordForm.password" type="password" show-password placeholder="至少8位" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="passwordVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="savePassword">重置</el-button>
      </template>
    </el-drawer>

    <!-- 新增/编辑组织 -->
    <el-dialog v-model="orgVisible" :title="editingOrg ? '编辑组织' : '新增组织'" width="560px">
      <el-form label-position="top">
        <el-form-item v-if="!editingOrg" label="组织编号" required>
          <el-input v-model="orgForm.organization_id" />
        </el-form-item>
        <el-form-item label="组织名称" required>
          <el-input v-model="orgForm.name" />
        </el-form-item>
        <el-form-item label="上级组织">
          <el-select v-model="orgForm.parent_organization_id" clearable style="width: 100%">
            <el-option v-for="item in organizations.filter((org) => org.organization_id !== editingOrg?.organization_id)" :key="item.organization_id" :label="item.name" :value="item.organization_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="orgForm.status" active-value="ACTIVE" inactive-value="DISABLED" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="orgVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveOrg">保存</el-button>
      </template>
    </el-dialog>

    <!-- 新增/编辑职位 -->
    <el-dialog v-model="positionVisible" :title="editingPosition ? '编辑职位' : '新增职位'" width="480px">
      <el-form label-position="top">
        <el-form-item v-if="!editingPosition" label="职位编码" required>
          <el-input v-model="positionForm.position_code" placeholder="如: MANAGER" />
        </el-form-item>
        <el-form-item label="职位名称" required>
          <el-input v-model="positionForm.name" placeholder="如: 部门经理" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="positionForm.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="positionVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="savePosition">保存</el-button>
      </template>
    </el-dialog>

    <!-- 分配用户职位 -->
    <el-dialog v-model="userPositionVisible" title="分配职位" width="480px">
      <el-form label-position="top">
        <el-form-item label="用户">
          <el-input :model-value="selectedUser?.display_name" disabled />
        </el-form-item>
        <el-form-item label="组织" required>
          <el-select v-model="userPositionForm.organization_id" style="width: 100%">
            <el-option v-for="item in allOrganizations" :key="item.organization_id" :label="item.name" :value="item.organization_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="职位" required>
          <el-select v-model="userPositionForm.position_code" style="width: 100%">
            <el-option v-for="item in allPositions" :key="item.position_code" :label="item.name" :value="item.position_code" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="userPositionForm.is_primary">设为主职位</el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="userPositionVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveUserPosition">分配</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.management-tabs { display: flex; gap: 24px; }
.management-tabs button { position: relative; height: 46px; padding: 0 1px; border: 0; color: var(--ink-500); background: transparent; font-size: 15px; cursor: pointer; }
.management-tabs button.active { color: var(--ink-900); font-weight: 650; }
.management-tabs button.active::after { position: absolute; right: 0; bottom: 0; left: 0; height: 2px; background: var(--accent-500); content: ''; }
.list-panel { min-height: 520px; overflow: hidden; }
.inline-tag { margin: 2px 4px 2px 0; }
.primary-icon { margin-left: 2px; color: var(--warning); }
.position-list { display: grid; gap: 8px; }
.position-item { display: flex; align-items: center; gap: 8px; padding: 8px 12px; background: var(--bg-secondary); border-radius: 6px; }
</style>
