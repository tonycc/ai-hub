<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { usePrototypeStore } from '../stores/prototype'

const store = usePrototypeStore()
const router = useRouter()
const keyword = ref('')
const statusFilter = ref('全部')

const filteredApps = computed(() => {
  const key = keyword.value.trim().toLowerCase()
  return store.state.applications.filter((item) => {
    const matchesKeyword = !key || `${item.name}${item.description}${item.owner}`.toLowerCase().includes(key)
    const matchesStatus = statusFilter.value === '全部' || item.status === statusFilter.value
    return matchesKeyword && matchesStatus
  })
})
</script>

<template>
  <div class="page-shell">
    <PageHeader title="应用中心" description="管理独立应用的环境、入口、回调、接入能力和生命周期；平台不承载应用领域实现。">
      <el-input v-model="keyword" prefix-icon="Search" clearable placeholder="搜索应用或负责人" style="width: 280px" />
      <el-radio-group v-model="statusFilter" size="default">
        <el-radio-button label="全部" value="全部" />
        <el-radio-button label="内部验证" value="内部验证" />
        <el-radio-button label="待配置" value="待配置" />
        <el-radio-button label="已停用" value="已停用" />
      </el-radio-group>
      <template #actions>
        <el-button><el-icon><Document /></el-icon>接入规范</el-button>
        <el-button type="primary"><el-icon><Plus /></el-icon>注册应用</el-button>
      </template>
    </PageHeader>

    <section class="page-section">
      <div class="applications-grid">
        <article v-for="app in filteredApps" :key="app.code" class="app-card surface-panel">
          <div class="app-card__top">
            <span class="app-card__icon" :style="{ '--app-color': app.color }"><el-icon><component :is="app.icon" /></el-icon></span>
            <div class="app-card__identity">
              <div><h2>{{ app.name }}</h2><StatusTag :status="app.status" /></div>
              <p>{{ app.description }}</p>
            </div>
            <el-dropdown>
              <el-button text circle><el-icon><MoreFilled /></el-icon></el-button>
              <template #dropdown><el-dropdown-menu><el-dropdown-item>应用设置</el-dropdown-item><el-dropdown-item>查看版本</el-dropdown-item><el-dropdown-item>权限配置</el-dropdown-item></el-dropdown-menu></template>
            </el-dropdown>
          </div>

          <dl class="app-card__facts">
            <div><dt>负责人</dt><dd>{{ app.owner }}</dd></div>
            <div><dt>认证用户</dt><dd>{{ app.activeUsers || '—' }}</dd></div>
            <div><dt>接入能力</dt><dd>{{ app.capabilities }}</dd></div>
            <div><dt>健康状态</dt><dd>{{ app.health ? `${app.health}%` : '未检测' }}</dd></div>
          </dl>

          <div class="app-card__architecture">
            <span>应用边界</span>
            <div><em>app_{{ app.code }}</em><em>独立迁移</em><em>公开契约</em></div>
          </div>

          <div class="app-card__footer">
            <span v-if="app.health" class="health"><i /> 运行正常</span>
            <span v-else class="muted">尚未启用健康检查</span>
            <el-button type="primary" link @click="router.push(app.route)">查看配置<el-icon><ArrowRight /></el-icon></el-button>
          </div>
        </article>

        <button type="button" class="new-app-card">
          <span><el-icon><Plus /></el-icon></span>
          <strong>注册新应用</strong>
          <small>从标准脚手架开始，复用平台公共能力</small>
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.applications-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.app-card {
  min-width: 0;
  padding: 17px;
}

.app-card__top {
  display: grid;
  grid-template-columns: 46px minmax(0, 1fr) auto;
  align-items: start;
  gap: 11px;
}

.app-card__icon {
  display: grid;
  width: 44px;
  height: 44px;
  border-radius: 10px;
  color: var(--app-color);
  background: color-mix(in srgb, var(--app-color) 10%, white);
  font-size: 21px;
  place-items: center;
}

.app-card__identity > div {
  display: flex;
  align-items: center;
  gap: 8px;
}

.app-card__identity h2 {
  margin: 0;
  color: var(--ink-900);
  font-size: 14px;
}

.app-card__identity p {
  margin: 5px 0 0;
  color: var(--ink-500);
  font-size: 12px;
  line-height: 1.5;
}

.app-card__facts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin: 18px 0 0;
  padding: 13px 0;
  border-top: 1px solid #edf0f2;
  border-bottom: 1px solid #edf0f2;
}

.app-card__facts div {
  min-width: 0;
  padding: 0 8px;
  border-left: 1px solid #edf0f2;
}

.app-card__facts div:first-child {
  padding-left: 0;
  border-left: 0;
}

.app-card__facts dt {
  color: var(--ink-500);
  font-size: 10px;
}

.app-card__facts dd {
  margin: 5px 0 0;
  overflow: hidden;
  color: var(--ink-900);
  font-size: 12px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-card__architecture {
  margin-top: 13px;
}

.app-card__architecture > span {
  display: block;
  margin-bottom: 7px;
  color: var(--ink-500);
  font-size: 10px;
}

.app-card__architecture div {
  display: flex;
  gap: 6px;
  overflow-x: auto;
}

.app-card__architecture em {
  padding: 4px 7px;
  border: 1px solid #e1e6e9;
  border-radius: 4px;
  color: #61717c;
  background: #f8f9fa;
  font-size: 10px;
  font-style: normal;
  white-space: nowrap;
}

.app-card__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 30px;
  margin-top: 11px;
  font-size: 11px;
}

.health {
  color: #52806d;
}

.health i {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 4px;
  border-radius: 50%;
  background: #45906e;
}

.new-app-card {
  display: grid;
  min-height: 272px;
  place-content: center;
  justify-items: center;
  gap: 8px;
  border: 1px dashed #c8d1d6;
  border-radius: 8px;
  color: var(--ink-500);
  background: rgb(255 255 255 / 45%);
  cursor: pointer;
}

.new-app-card:hover {
  border-color: #adbbc3;
  background: #fff;
}

.new-app-card > span {
  display: grid;
  width: 38px;
  height: 38px;
  border: 1px solid #dce2e6;
  border-radius: 50%;
  background: #fff;
  font-size: 16px;
  place-items: center;
}

.new-app-card strong {
  color: var(--ink-700);
  font-size: 11px;
}

.new-app-card small {
  max-width: 220px;
  color: var(--ink-500);
  font-size: 11px;
  line-height: 1.5;
  text-align: center;
}

@media (max-width: 1250px) {
  .applications-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 800px) {
  .applications-grid { grid-template-columns: 1fr; }
}
</style>
