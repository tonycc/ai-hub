<script setup>
defineProps({
  loading: { type: Boolean, default: false },
  error: { type: Object, default: null },
  empty: { type: Boolean, default: false },
  emptyText: { type: String, default: '暂无数据' },
})

defineEmits(['retry'])
</script>

<template>
  <div v-if="loading" class="api-state" aria-live="polite">
    <el-skeleton :rows="6" animated />
  </div>
  <el-result v-else-if="error" icon="error" title="加载失败" :sub-title="error.message">
    <template #extra>
      <el-button type="primary" @click="$emit('retry')">重试</el-button>
      <p v-if="error.requestId" class="request-id mono">Request ID: {{ error.requestId }}</p>
    </template>
  </el-result>
  <el-empty v-else-if="empty" :description="emptyText" :image-size="72" />
  <slot v-else />
</template>

<style scoped>
.api-state { padding: 24px; }
.request-id { margin: 10px 0 0; color: var(--ink-500); font-size: 11px; }
</style>
