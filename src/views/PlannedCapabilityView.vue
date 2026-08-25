<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PageHeader from '../components/PageHeader.vue'

const route = useRoute()
const router = useRouter()

const capabilities = {
  semantics: {
    title: '企业语义中心',
    phase: 'V1.1 / M5',
    description: '语义目录、版本、来源绑定和变更影响尚未纳入当前生产基线。',
    conditions: ['形成明确的跨应用语义治理需求', '冻结对象、版本与来源绑定公开契约', '完成权限、审计、迁移与回滚验收设计'],
  },
  'ai-center': {
    title: 'AI 治理中心',
    phase: 'V1.2 / M6',
    description: '模型、知识、工具、评测、证据和运行审计尚未纳入当前生产基线。',
    conditions: ['明确 AI 能力的风险分级和责任边界', '冻结工具调用、证据与评测公开契约', '完成权限、审计、人工确认与紧急停用验收设计'],
  },
}

const capability = computed(() => capabilities[route.name] || capabilities.semantics)
</script>

<template>
  <div class="page-shell planned-page">
    <PageHeader :title="capability.title" :description="capability.description">
      <template #actions>
        <el-button type="primary" @click="router.push('/platform/developer')">
          <el-icon><Back /></el-icon>返回能力总览
        </el-button>
      </template>
    </PageHeader>

    <section class="surface-panel planned-panel page-section">
      <el-result icon="info" title="能力尚未启用" :sub-title="capability.description">
        <template #extra>
          <el-tag type="info" effect="plain">{{ capability.phase }}</el-tag>
        </template>
      </el-result>
      <div class="planned-boundary">
        <div>
          <h2>当前边界</h2>
          <p>当前版本不提供该能力的 API、存储结构、后台任务、配置写入或正式数据。</p>
        </div>
        <div>
          <h2>启动条件</h2>
          <ol>
            <li v-for="item in capability.conditions" :key="item">{{ item }}</li>
          </ol>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.planned-panel { min-height: 520px; padding: 28px; }
.planned-boundary { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); max-width: 920px; margin: 0 auto; border-top: 1px solid var(--line); }
.planned-boundary > div { padding: 22px; }
.planned-boundary > div + div { border-left: 1px solid var(--line); }
.planned-boundary h2 { margin: 0; color: var(--ink-900); font-size: 15px; }
.planned-boundary p, .planned-boundary ol { margin: 9px 0 0; color: var(--ink-500); font-size: 13px; line-height: 1.7; }
.planned-boundary ol { padding-left: 20px; }
@media (max-width: 720px) { .planned-panel { padding: 18px; } .planned-boundary { grid-template-columns: 1fr; } .planned-boundary > div + div { border-top: 1px solid var(--line); border-left: 0; } }
</style>
