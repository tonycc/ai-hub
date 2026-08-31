<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { platformCapabilityGroups } from '../data/platformCapabilities'
import { apiRequest } from '../services/platformApi'
import { usePortalSession } from '../stores/session'

const route = useRoute()
const router = useRouter()
const session = usePortalSession()
const collapsed = ref(false)
const mobileMenuVisible = ref(false)
const searchVisible = ref(false)
const searchText = ref('')
const registeredApplications = ref([])
const applicationSearchUnavailable = ref(false)

const navGroups = [
  {
    title: '平台门户',
    items: [
      { label: '平台首页', path: '/', icon: 'HomeFilled' },
      { label: '应用中心', path: '/applications', icon: 'Grid' },
      { label: '通知中心', path: '/platform/notifications', icon: 'Bell', badge: true },
    ],
  },
  {
    title: '平台治理',
    items: [
      { label: '用户与组织', path: '/platform/identity', icon: 'UserFilled' },
      { label: '权限与安全', path: '/platform/permissions', icon: 'Lock' },
      { label: '接入治理', path: '/platform/integrations', icon: 'Connection' },
      { label: '审计中心', path: '/platform/audit', icon: 'Tickets' },
    ],
  },
  {
    title: '运行与研发',
    items: [
      { label: '运维中心', path: '/platform/operations', icon: 'Monitor' },
      { label: '数据接入', path: '/platform/ingest', icon: 'Coin' },
      { label: '平台配置', path: '/platform/settings', icon: 'SetUp' },
      { label: '开发者中心', path: '/platform/developer', icon: 'Tools' },
      { label: '平台用户', path: '/platform/platform-users', icon: 'UserFilled' },
    ],
  },
]

const routePermissions = {
  '/applications': 'platform.application.read',
  '/platform/notifications': 'platform.notification.read',
  '/platform/identity': 'platform.identity.read',
  '/platform/permissions': 'platform.authorization.read',
  '/platform/integrations': 'platform.application.read',
  '/platform/audit': 'platform.audit.read',
  '/platform/operations': 'platform.operations.read',
  '/platform/ingest': 'platform.ingest.read',
  '/platform/settings': 'platform.operations.read',
  '/platform/developer': 'platform.developer.read',
  '/platform/platform-users': 'platform.identity.read',
}

const visibleNavGroups = computed(() => navGroups.map((group) => ({
  ...group,
  items: group.items.filter((item) => {
    const permission = routePermissions[item.path]
    return !permission || session.hasPermission(permission)
  }),
})).filter((group) => group.items.length))

const canViewIdentity = computed(() => session.hasPermission('platform.identity.read'))
const canViewPermissions = computed(() => session.hasPermission('platform.authorization.read'))

const userInitial = computed(() => session.principal.value?.display_name?.slice(0, 1) || '访')
const unreadCount = ref(0)

const breadcrumbItems = computed(() => route.meta.breadcrumb || [])

const activeMenu = computed(() => route.path)

const searchResults = computed(() => {
  const keyword = searchText.value.trim().toLowerCase()
  const appResults = registeredApplications.value.map((item) => ({
    type: '应用',
    title: item.name,
    subtitle: item.description || item.application_id,
    path: '/applications',
    icon: 'Grid',
  }))
  const capabilityResults = platformCapabilityGroups.flatMap((group) => group.items).map((item) => ({
    type: '平台能力',
    title: item.name,
    subtitle: `${item.code} · ${item.description}`,
    path: item.route,
    icon: 'DataAnalysis',
  }))
  const results = [...appResults, ...capabilityResults]
  if (!keyword) return results.slice(0, 10)
  return results.filter((item) => `${item.title}${item.subtitle}${item.type}`.toLowerCase().includes(keyword)).slice(0, 12)
})

function navigate(path) {
  searchVisible.value = false
  mobileMenuVisible.value = false
  searchText.value = ''
  router.push(path)
}

async function handleUserCommand(command) {
  if (command === 'permissions') navigate('/platform/permissions')
  if (command === 'profile') navigate('/platform/identity')
  if (command === 'logout') {
    try {
      await session.logout()
      window.location.assign('/auth/logout')
    } catch (error) {
      ElMessage.error(error.message || '退出登录失败')
    }
  }
}

function handleGlobalShortcut(event) {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    searchVisible.value = true
  }
}

async function handleVisibilityRefresh() {
  if (document.visibilityState !== 'visible') return
  try {
    await session.loadSession({ force: true })
  } catch (error) {
    if (error.status !== 401) ElMessage.error(error.message || '平台会话加载失败')
  }
}

async function loadSearchApplications() {
  if (!session.hasPermission('platform.application.read')) return
  try {
    registeredApplications.value = (await apiRequest('applications')).items
  } catch {
    applicationSearchUnavailable.value = true
  }
}

onMounted(async () => {
  window.addEventListener('keydown', handleGlobalShortcut)
  document.addEventListener('visibilitychange', handleVisibilityRefresh)
  try {
    await session.loadSession()
    await loadSearchApplications()
  } catch (error) {
    if (error.status === 401) {
      window.location.assign(`/auth/login?return_to=${encodeURIComponent(route.fullPath)}`)
      return
    }
    ElMessage.error(error.message || '平台会话加载失败')
  }
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleGlobalShortcut)
  document.removeEventListener('visibilitychange', handleVisibilityRefresh)
})
</script>

<template>
  <el-container class="platform-layout">
    <el-aside class="platform-aside" :width="collapsed ? '72px' : '232px'">
      <div class="brand" :class="{ 'brand--collapsed': collapsed }">
        <div class="brand__mark"><span>AI</span></div>
        <div v-if="!collapsed" class="brand__copy">
          <strong>AI Hub</strong>
          <small>AI HUB</small>
        </div>
      </div>

      <nav class="main-nav" aria-label="主导航">
        <div v-for="group in visibleNavGroups" :key="group.title" class="nav-group">
          <span v-if="!collapsed" class="nav-group__title">{{ group.title }}</span>
          <el-tooltip
            v-for="item in group.items"
            :key="item.path"
            :content="item.label"
            placement="right"
            :disabled="!collapsed"
          >
            <button
              type="button"
              class="nav-item"
              :class="{ 'nav-item--active': activeMenu === item.path }"
              @click="navigate(item.path)"
            >
              <el-icon><component :is="item.icon" /></el-icon>
              <span v-if="!collapsed">{{ item.label }}</span>
              <em v-if="!collapsed && item.badge && unreadCount">{{ unreadCount }}</em>
            </button>
          </el-tooltip>
        </div>
      </nav>

      <div class="aside-footer">
        <button type="button" class="collapse-button" @click="collapsed = !collapsed">
          <el-icon><Fold v-if="!collapsed" /><Expand v-else /></el-icon>
          <span v-if="!collapsed">收起导航</span>
        </button>
        <div v-if="!collapsed" class="environment-label"><i /> 平台基线 · M4</div>
      </div>
    </el-aside>

    <el-container class="platform-main-wrap">
      <el-header class="platform-header" height="64px">
        <div class="header-left">
          <el-button class="mobile-menu-button" text circle @click="mobileMenuVisible = true">
            <el-icon><Menu /></el-icon>
          </el-button>
          <el-breadcrumb separator="/">
            <el-breadcrumb-item v-for="item in breadcrumbItems" :key="item">{{ item }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <button type="button" class="global-search" @click="searchVisible = true">
          <el-icon><Search /></el-icon>
          <span>搜索应用登记或平台能力</span>
          <kbd>⌘ K</kbd>
        </button>

        <div class="header-actions">
          <button type="button" class="plant-selector">
            <el-icon><Monitor /></el-icon>
            <span>集成环境</span>
            <el-icon class="plant-selector__arrow"><ArrowDown /></el-icon>
          </button>
          <el-tooltip content="消息中心" placement="bottom">
          <el-badge :value="unreadCount" :max="9" :hidden="!unreadCount">
              <button type="button" class="icon-button" @click="navigate('/platform/notifications')"><el-icon><Bell /></el-icon></button>
            </el-badge>
          </el-tooltip>
          <el-dropdown @command="handleUserCommand">
            <button type="button" class="user-button">
              <el-avatar :size="30">{{ userInitial }}</el-avatar>
              <span>{{ session.principal.value?.display_name || '正在加载' }}</span>
              <el-icon><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-if="canViewIdentity" command="profile">个人与身份</el-dropdown-item>
                <el-dropdown-item v-if="canViewPermissions" command="permissions">权限与安全</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="platform-content">
        <router-view />
      </el-main>
    </el-container>

    <el-drawer v-model="mobileMenuVisible" direction="ltr" size="280px" title="导航">
      <nav class="mobile-nav">
        <div v-for="group in visibleNavGroups" :key="group.title">
          <span>{{ group.title }}</span>
          <button v-for="item in group.items" :key="item.path" type="button" @click="navigate(item.path)">
            <el-icon><component :is="item.icon" /></el-icon>{{ item.label }}
          </button>
        </div>
      </nav>
    </el-drawer>

    <el-dialog v-model="searchVisible" width="620px" class="search-dialog" :show-close="false">
      <template #header>
        <el-input v-model="searchText" size="large" placeholder="搜索应用登记、能力编号或平台模块" autofocus>
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
      </template>
      <div class="search-results">
        <p>{{ searchText ? '搜索结果' : '最近访问' }}</p>
        <small v-if="applicationSearchUnavailable" class="search-results__notice">应用索引暂不可用，平台能力仍可搜索</small>
        <button v-for="item in searchResults" :key="`${item.type}-${item.title}`" type="button" @click="navigate(item.path)">
          <span class="search-result__icon"><el-icon><component :is="item.icon" /></el-icon></span>
          <span class="search-result__copy"><strong>{{ item.title }}</strong><small>{{ item.subtitle }}</small></span>
          <el-tag size="small" type="info" effect="plain">{{ item.type }}</el-tag>
          <el-icon><Right /></el-icon>
        </button>
        <el-empty v-if="!searchResults.length" description="没有找到匹配内容" :image-size="60" />
      </div>
    </el-dialog>
  </el-container>
</template>

<style scoped>
.platform-layout {
  min-height: 100vh;
}

.platform-aside {
  position: fixed;
  z-index: 20;
  top: 0;
  bottom: 0;
  left: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  color: #fff;
  background: var(--brand-900);
  transition: width 0.2s ease;
}

.brand {
  display: flex;
  align-items: center;
  height: 64px;
  gap: 10px;
  padding: 0 18px;
  border-bottom: 1px solid rgb(255 255 255 / 8%);
}

.brand--collapsed {
  justify-content: center;
  padding: 0;
}

.brand__mark {
  position: relative;
  display: grid;
  flex: 0 0 34px;
  width: 34px;
  height: 34px;
  border: 1px solid rgb(255 255 255 / 18%);
  border-radius: 8px;
  overflow: hidden;
  color: #fff;
  background: #263947;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.04em;
  place-items: center;
}

.brand__mark::after {
  position: absolute;
  right: -5px;
  bottom: -5px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--accent-500);
  content: "";
}

.brand__copy {
  display: grid;
  white-space: nowrap;
}

.brand__copy strong {
  font-size: 14px;
  letter-spacing: 0.08em;
}

.brand__copy small {
  margin-top: 2px;
  color: #8fa0ad;
  font-size: 10px;
  letter-spacing: 0.25em;
}

.main-nav {
  flex: 1;
  padding: 14px 10px;
  overflow-y: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.main-nav::-webkit-scrollbar {
  display: none;
  width: 0;
  height: 0;
}

.nav-group + .nav-group {
  margin-top: 18px;
}

.nav-group__title {
  display: block;
  margin: 0 9px 7px;
  color: #6f8392;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.14em;
}

.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  width: 100%;
  height: 40px;
  gap: 11px;
  padding: 0 11px;
  border: 0;
  border-radius: 6px;
  color: #aebbc4;
  background: transparent;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: 0.15s ease;
}

.nav-item:hover {
  color: #fff;
  background: rgb(255 255 255 / 5%);
}

.nav-item--active {
  color: #fff;
  background: #2a3d4a;
}

.nav-item--active::before {
  position: absolute;
  left: 0;
  width: 3px;
  height: 18px;
  border-radius: 0 3px 3px 0;
  background: var(--accent-500);
  content: "";
}

.nav-item .el-icon {
  flex: 0 0 18px;
  font-size: 16px;
}

.nav-item span {
  flex: 1;
  white-space: nowrap;
}

.nav-item em {
  min-width: 19px;
  padding: 2px 5px;
  border-radius: 9px;
  color: #f4c8b2;
  background: rgb(211 92 39 / 19%);
  font-size: 10px;
  font-style: normal;
  text-align: center;
}

.aside-footer {
  padding: 10px;
  border-top: 1px solid rgb(255 255 255 / 7%);
}

.collapse-button {
  display: flex;
  align-items: center;
  width: 100%;
  height: 34px;
  gap: 10px;
  padding: 0 11px;
  border: 0;
  border-radius: 5px;
  color: #8fa0ac;
  background: transparent;
  font-size: 12px;
  cursor: pointer;
}

.collapse-button:hover {
  color: #fff;
  background: rgb(255 255 255 / 5%);
}

.environment-label {
  margin: 7px 11px 0;
  color: #647884;
  font-size: 10px;
}

.environment-label i {
  display: inline-block;
  width: 5px;
  height: 5px;
  margin-right: 5px;
  border-radius: 50%;
  background: #64a486;
}

.platform-main-wrap {
  min-width: 0;
  margin-left: v-bind("collapsed ? '72px' : '232px'");
  transition: margin-left 0.2s ease;
}

.platform-header {
  position: sticky;
  z-index: 15;
  top: 0;
  display: grid;
  grid-template-columns: minmax(180px, 1fr) minmax(300px, 560px) minmax(260px, 1fr);
  align-items: center;
  gap: 20px;
  padding: 0 var(--header-gutter);
  border-bottom: 1px solid #dde3e7;
  background: rgb(255 255 255 / 94%);
  backdrop-filter: blur(12px);
}

.header-left {
  display: flex;
  align-items: center;
  min-width: 0;
}

.mobile-menu-button {
  display: none;
}

.global-search {
  display: grid;
  grid-template-columns: 20px 1fr auto;
  align-items: center;
  width: 100%;
  height: 36px;
  gap: 7px;
  padding: 0 9px 0 12px;
  border: 1px solid #dce2e6;
  border-radius: 6px;
  color: #84919a;
  background: #f7f8f9;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.global-search:hover {
  border-color: #c8d0d5;
  background: #fff;
}

.global-search kbd {
  padding: 2px 6px;
  border: 1px solid #d7dde1;
  border-radius: 4px;
  color: #8c979e;
  background: #fff;
  font-family: inherit;
  font-size: 10px;
}

.header-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
}

.plant-selector,
.user-button,
.icon-button {
  display: flex;
  align-items: center;
  border: 0;
  color: #53636f;
  background: transparent;
  cursor: pointer;
}

.plant-selector {
  height: 32px;
  gap: 6px;
  padding: 0 9px;
  border: 1px solid #e0e5e8;
  border-radius: 5px;
  font-size: 12px;
}

.plant-selector__arrow {
  margin-left: 2px;
  color: #9aa4aa;
}

.icon-button {
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 6px;
  font-size: 16px;
}

.icon-button:hover {
  background: #f2f4f5;
}

.user-button {
  gap: 7px;
  font-size: 13px;
}

.user-button :deep(.el-avatar) {
  color: #fff;
  background: #4c6271;
  font-size: 12px;
}

.platform-content {
  min-height: calc(100vh - 64px);
  padding: 0 var(--content-gutter) 24px;
  overflow: visible;
  background: #eef1f4;
}

.mobile-nav {
  display: grid;
  gap: 22px;
}

.mobile-nav > div {
  display: grid;
  gap: 4px;
}

.mobile-nav > div > span {
  margin: 0 9px 5px;
  color: var(--ink-500);
  font-size: 11px;
  font-weight: 700;
}

.mobile-nav button {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px;
  border: 0;
  border-radius: 6px;
  color: var(--ink-700);
  background: transparent;
  text-align: left;
}

.search-results > p {
  margin: 0 0 8px;
  color: var(--ink-500);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.search-results__notice {
  display: block;
  margin: -2px 8px 8px;
  color: var(--warning);
  font-size: 11px;
}

.search-results > button {
  display: grid;
  grid-template-columns: 34px 1fr auto 18px;
  align-items: center;
  width: 100%;
  gap: 10px;
  padding: 9px 8px;
  border: 0;
  border-radius: 6px;
  color: var(--ink-500);
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.search-results > button:hover {
  background: #f5f7f8;
}

.search-result__icon {
  display: grid;
  width: 32px;
  height: 32px;
  border: 1px solid var(--line);
  border-radius: 6px;
  color: var(--accent-600);
  background: #fff;
  place-items: center;
}

.search-result__copy {
  display: grid;
  gap: 3px;
}

.search-result__copy strong {
  color: var(--ink-900);
  font-size: 13px;
}

.search-result__copy small {
  color: var(--ink-500);
  font-size: 11px;
}

@media (max-width: 1100px) {
  .platform-header {
    grid-template-columns: minmax(160px, 1fr) minmax(240px, 400px) auto;
  }

  .plant-selector span,
  .user-button > span {
    display: none;
  }
}

@media (max-width: 820px) {
  :global(:root) {
    --content-gutter: 14px;
    --header-gutter: 14px;
  }

  .platform-aside {
    display: none;
  }

  .platform-main-wrap {
    margin-left: 0;
  }

  .platform-header {
    grid-template-columns: 1fr auto;
    padding: 0 14px;
  }

  .mobile-menu-button {
    display: inline-flex;
    margin-right: 4px;
  }

  .global-search {
    display: none;
  }

  .plant-selector {
    display: none;
  }

  .platform-content {
    padding: 0 var(--content-gutter) 24px;
  }

}
</style>
