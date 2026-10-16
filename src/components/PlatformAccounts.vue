<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import ApiState from './ApiState.vue'
import PageHeader from './PageHeader.vue'
import { apiRequest } from '../services/platformApi'
import { usePortalSession } from '../stores/session'

const session = usePortalSession()
const loading = ref(false)
const error = ref(null)
const assignments = ref([])
const users = ref([])
const roles = ref([])
const applications = ref([])
const visible = ref(false)
const saving = ref(false)
const form = reactive({ user_id: '', role_code: '', application_id: null })
const canWrite = computed(() => session.hasPermission('platform.identity.write'))
async function load() {
  loading.value = true
  error.value = null
  try {
    const [assignmentResponse, userResponse, roleResponse] = await Promise.all([
      apiRequest('platform-role-assignments'), apiRequest('users?status=ACTIVE'), apiRequest('platform-roles'),
    ])
    assignments.value = assignmentResponse.items
    users.value = userResponse.items
    roles.value = roleResponse.items.filter((role) => role.status === 'ACTIVE' && ['PLATFORM_ADMIN', 'APPLICATION_DEVELOPER'].includes(role.role_code))
    if (session.hasPermission('platform.application.read')) applications.value = (await apiRequest('applications')).items
  } catch (caught) { error.value = caught } finally { loading.value = false }
}
function openAssignment() {
  Object.assign(form, { user_id: '', role_code: '', application_id: null })
  visible.value = true
}
async function save() {
  if (!form.user_id || !form.role_code) { ElMessage.error('请选择用户和角色'); return }
  if (form.role_code === 'APPLICATION_DEVELOPER' && !form.application_id) { ElMessage.error('请选择应用范围'); return }
  saving.value = true
  try {
    await apiRequest('platform-role-assignments', { method: 'POST', body: form })
    visible.value = false
    ElMessage.success('平台管理权限已分配')
    await load()
  } catch (caught) { ElMessage.error(caught.message) } finally { saving.value = false }
}
async function revoke(row) {
  try {
    await ElMessageBox.confirm(`撤销 ${row.display_name || row.subject} 的 ${row.role_code} 角色？`, '撤销平台管理权限', { type: 'warning' })
    await apiRequest(`platform-role-assignments/${row.assignment_id}`, { method: 'DELETE' })
    ElMessage.success('平台管理权限已撤销')
    await load()
  } catch (caught) { if (caught !== 'cancel' && caught !== 'close') ElMessage.error(caught.message) }
}
onMounted(load)
</script>
<template>
  <div class="page-shell">
    <PageHeader title="平台管理权限"><template #actions>
      <el-button @click="load">刷新</el-button>
      <el-button v-if="canWrite" type="primary" @click="openAssignment">分配角色</el-button>
    </template></PageHeader>
    <section class="surface-panel page-section">
      <ApiState :loading="loading" :error="error" :empty="!assignments.length" empty-text="暂无平台角色分配" @retry="load">
        <el-table :data="assignments" style="width: 100%">
          <el-table-column prop="display_name" label="用户" min-width="150" />
          <el-table-column prop="role_code" label="平台角色" min-width="180" />
          <el-table-column label="应用范围" min-width="160"><template #default="scope">{{ scope.row.application_id || '全部应用' }}</template></el-table-column>
          <el-table-column v-if="canWrite" label="操作" width="90"><template #default="scope"><el-button type="danger" link @click="revoke(scope.row)">撤销</el-button></template></el-table-column>
        </el-table>
      </ApiState>
    </section>
    <el-drawer v-model="visible" title="分配平台角色" size="min(560px, 96vw)" append-to-body>
      <el-form label-position="top">
        <el-form-item label="用户" required><el-select v-model="form.user_id" filterable style="width: 100%"><el-option v-for="user in users" :key="user.user_id" :value="user.user_id" :label="`${user.display_name} · ${user.subject}`" /></el-select></el-form-item>
        <el-form-item label="平台角色" required><el-select v-model="form.role_code" @change="form.application_id = null" style="width: 100%"><el-option v-for="role in roles" :key="role.role_code" :value="role.role_code" :label="role.name" /></el-select></el-form-item>
        <el-form-item v-if="form.role_code === 'APPLICATION_DEVELOPER'" label="应用范围" required><el-select v-model="form.application_id" clearable placeholder="请选择应用" style="width: 100%" @clear="form.application_id = null"><el-option v-for="app in applications" :key="app.application_id" :value="app.application_id" :label="app.name" /></el-select></el-form-item>
      </el-form>
      <template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">分配</el-button></template>
    </el-drawer>
  </div>
</template>
