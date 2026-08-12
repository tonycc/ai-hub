<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import PageHeader from '../components/PageHeader.vue'
import { messageItems } from '../data/platformCapabilities'

const router = useRouter()
const activeCategory = ref('全部消息')
const messages = ref(structuredClone(messageItems))
const selectedId = ref(messages.value[0]?.id)

const categories = computed(() => ['全部消息', ...new Set(messages.value.map((item) => item.category))].map((name) => ({
  name,
  count: name === '全部消息' ? messages.value.length : messages.value.filter((item) => item.category === name).length,
})))

const filteredMessages = computed(() => activeCategory.value === '全部消息' ? messages.value : messages.value.filter((item) => item.category === activeCategory.value))
const selectedMessage = computed(() => messages.value.find((item) => item.id === selectedId.value) || filteredMessages.value[0])
const unreadCount = computed(() => messages.value.filter((item) => item.unread).length)

function selectMessage(item) {
  selectedId.value = item.id
  item.unread = false
}

function markAllRead() {
  messages.value.forEach((item) => { item.unread = false })
  ElMessage.success('全部消息已标记为已读')
}
</script>

<template>
  <div class="page-shell message-page">
    <PageHeader title="通知中心" description="集中查看平台实施、安全、接入认证和运行状态通知。">
      <template #tabs>
        <div class="message-category-tabs">
          <button v-for="category in categories" :key="category.name" type="button" :class="{ active: activeCategory === category.name }" @click="activeCategory = category.name">
            {{ category.name }}<em>{{ category.count }}</em>
          </button>
        </div>
      </template>
      <span class="toolbar-count">{{ unreadCount }} 条未读</span>
      <template #actions><el-button @click="markAllRead"><el-icon><CircleCheck /></el-icon>全部已读</el-button><el-button type="primary"><el-icon><Setting /></el-icon>通知偏好</el-button></template>
    </PageHeader>

    <div class="message-layout page-section surface-panel">
      <section class="message-list">
        <div class="message-list__header"><strong>{{ activeCategory }}</strong><span>{{ filteredMessages.length }} 条</span></div>
        <button v-for="item in filteredMessages" :key="item.id" type="button" :class="{ active: selectedId === item.id, unread: item.unread }" @click="selectMessage(item)">
          <span class="message-icon" :class="`message-icon--${item.tone}`"><el-icon><component :is="item.icon" /></el-icon></span>
          <span class="message-copy"><strong>{{ item.title }}</strong><small>{{ item.summary }}</small><em>{{ item.app }} · {{ item.category }}</em></span>
          <span class="message-time">{{ item.time }}</span><i v-if="item.unread" />
        </button>
        <el-empty v-if="!filteredMessages.length" description="当前分类没有消息" :image-size="64" />
      </section>

      <main v-if="selectedMessage" class="message-detail">
        <header><div><span>{{ selectedMessage.category }}</span><h2>{{ selectedMessage.title }}</h2><small>{{ selectedMessage.app }} · {{ selectedMessage.time }}</small></div><el-button text circle><el-icon><MoreFilled /></el-icon></el-button></header>
        <div class="message-detail__body">
          <p>{{ selectedMessage.summary }}</p>
          <div class="message-context-card">
            <span><el-icon><Document /></el-icon></span>
            <div><small>关联平台对象</small><strong>{{ selectedMessage.context }}</strong><em>当前内容仅用于平台原型验证</em></div>
          </div>
          <div class="delivery-evidence"><span>消息送达记录</span><dl><div><dt>发送渠道</dt><dd>站内消息</dd></div><div><dt>消息模板</dt><dd class="mono">platform.notification.v2</dd></div><div><dt>送达状态</dt><dd>已送达</dd></div><div><dt>读取时间</dt><dd>刚刚</dd></div></dl></div>
        </div>
        <footer><el-button @click="ElMessage.success('消息已归档')">归档</el-button><el-button type="primary" @click="router.push(selectedMessage.route)">查看平台对象<el-icon><ArrowRight /></el-icon></el-button></footer>
      </main>
    </div>
  </div>
</template>

<style scoped>
.message-category-tabs { display: flex; flex: 1 1 100%; width: 100%; min-width: 0; flex-wrap: wrap; gap: 0 24px; overflow: visible; }
.message-category-tabs button { position: relative; flex: none; height: 46px; padding: 0 1px; border: 0; color: var(--ink-500); background: transparent; font-size: 16px; cursor: pointer; }
.message-category-tabs button:hover { color: var(--ink-900); }
.message-category-tabs button.active { color: var(--ink-900); font-weight: 650; }
.message-category-tabs button.active::after { position: absolute; right: 0; bottom: 0; left: 0; height: 2px; background: var(--accent-500); content: ""; }
.message-category-tabs em { margin-left: 5px; color: #89959d; font-size: 11px; font-style: normal; }
.toolbar-count { flex: none; color: var(--ink-500); font-size: 11px; white-space: nowrap; }
.message-layout { display: grid; grid-template-columns: minmax(300px, 0.85fr) minmax(340px, 1.15fr); min-height: 650px; overflow: hidden; }
.message-list { border-right: 1px solid var(--line); overflow-y: auto; }
.message-list__header { display: flex; align-items: center; justify-content: space-between; height: 52px; padding: 0 14px; border-bottom: 1px solid var(--line); }
.message-list__header strong { color: var(--ink-900); font-size: 13px; }
.message-list__header span { color: var(--ink-500); font-size: 11px; }
.message-list > button { position: relative; display: grid; grid-template-columns: 36px minmax(0, 1fr) auto; align-items: start; width: 100%; gap: 10px; padding: 13px; border: 0; border-bottom: 1px solid #edf0f2; color: var(--ink-500); background: #fff; text-align: left; cursor: pointer; }
.message-list > button:hover, .message-list > button.active { background: #f7f9fa; }
.message-list > button.active { box-shadow: inset 3px 0 var(--accent-500); }
.message-list > button.unread .message-copy strong { color: var(--ink-900); font-weight: 700; }
.message-list > button > i { position: absolute; top: 20px; right: 8px; width: 6px; height: 6px; border-radius: 50%; background: var(--accent-500); }
.message-icon { display: grid; width: 34px; height: 34px; border-radius: 7px; place-items: center; }
.message-icon--danger { color: #b54942; background: #faeceb; }
.message-icon--primary { color: #416f86; background: #eaf1f4; }
.message-icon--info { color: #6e7780; background: #eef1f3; }
.message-icon--success { color: #438167; background: #eaf4ef; }
.message-copy { display: grid; min-width: 0; gap: 4px; }
.message-copy strong { overflow: hidden; color: #4b5b66; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.message-copy small { display: -webkit-box; overflow: hidden; color: var(--ink-500); font-size: 11px; line-height: 1.5; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.message-copy em { color: #929da4; font-size: 10px; font-style: normal; }
.message-time { padding-right: 7px; color: #939da4; font-size: 10px; white-space: nowrap; }
.message-detail { display: grid; grid-template-rows: auto 1fr auto; min-width: 0; }
.message-detail > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 18px 20px; border-bottom: 1px solid var(--line); }
.message-detail header div { display: grid; gap: 5px; }
.message-detail header span { color: var(--accent-600); font-size: 11px; font-weight: 700; }
.message-detail h2 { margin: 0; color: var(--ink-900); font-size: 16px; line-height: 1.45; }
.message-detail header small { color: var(--ink-500); font-size: 11px; }
.message-detail__body { padding: 20px; }
.message-detail__body > p { margin: 0; color: #465762; font-size: 13px; line-height: 1.8; }
.message-context-card { display: grid; grid-template-columns: 38px 1fr; align-items: center; gap: 10px; margin-top: 20px; padding: 12px; border: 1px solid #dee5e8; border-radius: 7px; background: #f8fafb; }
.message-context-card > span { display: grid; width: 36px; height: 36px; border-radius: 7px; color: #416f86; background: #e8f0f3; place-items: center; }
.message-context-card > div { display: grid; gap: 3px; }
.message-context-card small, .message-context-card em { color: var(--ink-500); font-size: 10px; font-style: normal; }
.message-context-card strong { color: var(--ink-900); font-size: 12px; }
.delivery-evidence { margin-top: 24px; }
.delivery-evidence > span { color: var(--ink-500); font-size: 11px; font-weight: 700; }
.delivery-evidence dl { margin-top: 8px; border-top: 1px solid #e8ecee; }
.delivery-evidence dl div { display: flex; justify-content: space-between; gap: 12px; padding: 10px 0; border-bottom: 1px solid #edf0f2; }
.delivery-evidence dt, .delivery-evidence dd { margin: 0; color: var(--ink-500); font-size: 11px; }
.delivery-evidence dd { color: var(--ink-700); }
.message-detail > footer { display: flex; justify-content: flex-end; gap: 8px; padding: 14px 20px; border-top: 1px solid var(--line); }
@media (max-width: 1000px) { .message-layout { grid-template-columns: 1fr; } .message-detail { display: none; } }
@media (max-width: 650px) { .message-category-tabs { gap: 0 12px; } .message-category-tabs button { flex: none; } .message-layout { grid-template-columns: 1fr; } }
</style>
