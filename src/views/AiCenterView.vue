<script setup>
import { computed, ref } from 'vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'

const activeTab = ref('agents')
const agentKeyword = ref('')

const agents = [
  { name: '接入文档助手', description: '解释公开契约、SDK 示例和接入认证失败项', app: '开发者中心', level: 'L0 · 查看', icon: 'Document', color: '#416f86', runs: 0, adoption: '—', status: '待实施' },
  { name: '运行诊断助手', description: '基于平台指标和运行手册生成诊断建议', app: '运维中心', level: 'L1 · 建议', icon: 'Monitor', color: '#527a64', runs: 0, adoption: '—', status: '待实施' },
  { name: '审计检索助手', description: '在当前权限范围内检索平台审计证据', app: '审计中心', level: 'L0 · 查看', icon: 'Search', color: '#735f84', runs: 0, adoption: '—', status: '待实施' },
]

const runs = [
  { id: '尚未启用', agent: '—', user: '—', task: 'V1.2 前不产生正式运行记录', result: '未运行', evidence: 0, model: '—', duration: '—', time: '—' },
]

const filteredAgents = computed(() => {
  const key = agentKeyword.value.trim().toLowerCase()
  return agents.filter((agent) => !key || `${agent.name}${agent.description}${agent.app}`.toLowerCase().includes(key))
})
</script>

<template>
  <div class="page-shell ai-center-page">
    <PageHeader title="AI 员工中心" description="统一管理 AI 员工、工具、权限、证据和评测，业务动作始终通过注册能力执行。">
      <template #tabs>
        <div class="ai-tabs">
          <button :class="{ active: activeTab === 'agents' }" @click="activeTab = 'agents'">AI 员工</button>
          <button :class="{ active: activeTab === 'runs' }" @click="activeTab = 'runs'">运行记录</button>
          <button :class="{ active: activeTab === 'tools' }" @click="activeTab = 'tools'">工具与能力</button>
          <button :class="{ active: activeTab === 'evals' }" @click="activeTab = 'evals'">评测集</button>
        </div>
      </template>
      <el-input v-if="activeTab === 'agents'" v-model="agentKeyword" prefix-icon="Search" clearable placeholder="搜索 AI 员工" style="width: 240px" />
      <template #actions>
        <el-button disabled><el-icon><DataAnalysis /></el-icon>评测报告</el-button>
        <el-button v-if="activeTab === 'runs'" disabled><el-icon><Download /></el-icon>导出审计</el-button>
        <el-button type="primary" disabled><el-icon><Plus /></el-icon>创建 AI 员工</el-button>
      </template>
    </PageHeader>

    <div class="ai-stats page-section">
      <span><small>今日运行</small><strong>0</strong><em>能力未启用</em></span>
      <span><small>离线评测集</small><strong>0</strong><em>等待 V1.2</em></span>
      <span><small>有效证据覆盖</small><strong>—</strong><em>尚无运行记录</em></span>
      <span><small>平均响应时间</small><strong>—</strong><em>尚未测量</em></span>
      <span><small>高风险误执行</small><strong>0</strong><em>无生产执行</em></span>
    </div>

    <section class="surface-panel ai-workspace page-section">
      <template v-if="activeTab === 'agents'">
        <div class="agent-grid">
          <article v-for="agent in filteredAgents" :key="agent.name" class="agent-card">
            <header>
              <span :style="{ '--agent-color': agent.color }"><el-icon><component :is="agent.icon" /></el-icon></span>
              <StatusTag :status="agent.status" />
              <el-button text circle><el-icon><MoreFilled /></el-icon></el-button>
            </header>
            <h2>{{ agent.name }}</h2>
            <p>{{ agent.description }}</p>
            <div class="agent-context"><span><el-icon><Grid /></el-icon>{{ agent.app }}</span><span><el-icon><Lock /></el-icon>{{ agent.level }}</span></div>
            <dl><div><dt>本月运行</dt><dd>{{ agent.runs }}</dd></div><div><dt>建议采纳率</dt><dd>{{ agent.adoption }}</dd></div><div><dt>证据覆盖</dt><dd>100%</dd></div></dl>
            <footer><span><i /> 后置能力</span><el-button type="primary" link disabled>尚未启用<el-icon><ArrowRight /></el-icon></el-button></footer>
          </article>
          <button type="button" class="new-agent"><span><el-icon><Plus /></el-icon></span><strong>创建 AI 员工</strong><small>绑定语义对象、工具、权限和评测集</small></button>
        </div>
      </template>

      <template v-else-if="activeTab === 'runs'">
        <el-table :data="runs">
          <el-table-column prop="id" label="运行编号" width="165"><template #default="scope"><span class="mono run-id">{{ scope.row.id }}</span></template></el-table-column>
          <el-table-column prop="agent" label="AI 员工" width="140" />
          <el-table-column prop="task" label="业务任务" min-width="230" />
          <el-table-column prop="user" label="发起人" width="90" />
          <el-table-column label="结果" width="90"><template #default="scope"><el-tag type="success" size="small" effect="light">{{ scope.row.result }}</el-tag></template></el-table-column>
          <el-table-column label="证据" width="90"><template #default="scope"><span class="evidence-count"><el-icon><Link /></el-icon>{{ scope.row.evidence }} 项</span></template></el-table-column>
          <el-table-column prop="model" label="模型版本" width="155" />
          <el-table-column prop="duration" label="耗时" width="80" />
          <el-table-column prop="time" label="时间" width="80" />
          <el-table-column label="操作" width="80"><template #default><el-button type="primary" link>证据链</el-button></template></el-table-column>
        </el-table>
      </template>

      <el-empty v-else description="该模块将在 AI 增强版逐步开放，当前原型先验证治理边界" />
    </section>
  </div>
</template>

<style scoped>
.ai-stats { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); border: 1px solid var(--line); border-radius: 8px; background: #fff; }
.ai-stats > span { display: grid; gap: 4px; padding: 13px 17px; border-left: 1px solid #edf0f2; }
.ai-stats > span:first-child { border-left: 0; }
.ai-stats small { color: var(--ink-500); font-size: 11px; }
.ai-stats strong { color: var(--ink-900); font-size: 20px; }
.ai-stats em { color: #468064; font-size: 10px; font-style: normal; }
.ai-workspace { min-height: 480px; overflow: hidden; }
.ai-tabs { display: flex; flex: 1 1 100%; width: 100%; min-width: 0; flex-wrap: wrap; gap: 0 24px; overflow: visible; }
.ai-tabs button { position: relative; flex: none; height: 46px; padding: 0 1px; border: 0; color: var(--ink-500); background: transparent; font-size: 16px; cursor: pointer; }
.ai-tabs button.active { color: var(--ink-900); font-weight: 650; }
.ai-tabs button.active::after { position: absolute; right: 0; bottom: -1px; left: 0; height: 2px; background: var(--accent-500); content: ""; }
.agent-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; padding: 16px; }
.agent-card { min-width: 0; padding: 15px; border: 1px solid var(--line); border-radius: 7px; background: #fff; }
.agent-card header { display: grid; grid-template-columns: 40px 1fr auto; align-items: start; gap: 8px; }
.agent-card header > span { display: grid; width: 38px; height: 38px; border-radius: 9px; color: var(--agent-color); background: color-mix(in srgb, var(--agent-color) 10%, white); font-size: 18px; place-items: center; }
.agent-card header :deep(.el-tag) { justify-self: end; }
.agent-card h2 { margin: 12px 0 0; color: var(--ink-900); font-size: 15px; }
.agent-card > p { min-height: 40px; margin: 6px 0 0; color: var(--ink-500); font-size: 11px; line-height: 1.6; }
.agent-context { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 11px; }
.agent-context span { display: flex; align-items: center; gap: 4px; padding: 5px 7px; border-radius: 4px; color: #687984; background: #f2f4f5; font-size: 10px; }
.agent-card dl { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; margin: 14px 0 0; background: #e8ecee; }
.agent-card dl div { display: grid; gap: 4px; padding: 8px 4px; background: #fafbfb; text-align: center; }
.agent-card dt { color: var(--ink-500); font-size: 10px; }
.agent-card dd { margin: 0; color: var(--ink-900); font-size: 12px; font-weight: 650; }
.agent-card footer { display: flex; align-items: center; justify-content: space-between; margin-top: 9px; color: #5d806f; font-size: 10px; }
.agent-card footer i { display: inline-block; width: 5px; height: 5px; margin-right: 4px; border-radius: 50%; background: #4b916f; }
.new-agent { display: grid; min-height: 268px; place-content: center; justify-items: center; gap: 8px; border: 1px dashed #cbd3d7; border-radius: 7px; color: var(--ink-500); background: #fafbfb; cursor: pointer; }
.new-agent > span { display: grid; width: 38px; height: 38px; border: 1px solid #dce2e5; border-radius: 50%; background: #fff; place-items: center; }
.new-agent strong { color: var(--ink-700); font-size: 13px; }
.new-agent small { max-width: 180px; font-size: 10px; line-height: 1.5; text-align: center; }
.run-id { color: #416b82; font-size: 11px; }
.evidence-count { display: flex; align-items: center; gap: 4px; color: #4d7387; font-size: 11px; }
@media (max-width: 1150px) {
  .agent-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 750px) {
  .ai-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .agent-grid { grid-template-columns: 1fr; }
  .page-header__filters :deep(.el-input), .page-header__filters :deep(.el-select) { width: 100% !important; }
}
</style>
