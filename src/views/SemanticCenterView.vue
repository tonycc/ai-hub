<script setup>
import { computed, ref } from 'vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { usePrototypeStore } from '../stores/prototype'

const store = usePrototypeStore()
const activeTab = ref('objects')
const selectedId = ref('example.record')
const keyword = ref('')

const selectedObject = computed(() => store.state.semanticObjects.find((item) => item.id === selectedId.value) || store.state.semanticObjects[0])
const filteredObjects = computed(() => {
  const key = keyword.value.trim().toLowerCase()
  return store.state.semanticObjects.filter((item) => !key || `${item.id}${item.name}${item.owner}`.toLowerCase().includes(key))
})

const objectProperties = [
  { name: 'id', label: '记录标识', type: 'string', required: true, sensitive: false },
  { name: 'name', label: '记录名称', type: 'string', required: true, sensitive: false },
  { name: 'state', label: '测试状态', type: 'enum', required: true, sensitive: false },
  { name: 'lock_version', label: '乐观锁版本', type: 'integer', required: true, sensitive: false },
  { name: 'updated_at', label: '更新时间', type: 'datetime', required: true, sensitive: false },
]

const bindings = [
  { object: 'example.record', application: '接入参考应用', contract: 'company.example.record.changed.v1', version: 'v1', status: 'draft', freshness: '测试事件' },
  { object: 'platform.application_registration', application: '应用中心', contract: 'GET /platform-api/v1/applications/{id}', version: 'v1-draft', status: 'reviewed', freshness: '实时' },
  { object: 'platform.notification_request', application: '通知中心', contract: 'POST /platform-api/v1/notifications', version: 'v1-draft', status: 'draft', freshness: '实时' },
]
</script>

<template>
  <div class="page-shell semantics-page">
    <PageHeader title="企业语义中心" description="用统一的业务语言连接应用数据、可执行行为与业务场景，为 AI 提供可追溯上下文。">
      <template #tabs>
        <div class="workbench-tabs">
          <button :class="{ active: activeTab === 'objects' }" @click="activeTab = 'objects'">语义对象</button>
          <button :class="{ active: activeTab === 'bindings' }" @click="activeTab = 'bindings'">来源绑定</button>
          <button :class="{ active: activeTab === 'behaviors' }" @click="activeTab = 'behaviors'">行为与规则</button>
          <button :class="{ active: activeTab === 'packages' }" @click="activeTab = 'packages'">领域语义包</button>
        </div>
      </template>
      <template #default>
        <el-input v-if="activeTab === 'objects'" v-model="keyword" prefix-icon="Search" clearable placeholder="搜索语义对象" />
      </template>
      <template #actions>
        <el-button v-if="activeTab === 'bindings'" type="primary" plain><el-icon><Plus /></el-icon>新建绑定</el-button>
        <el-button><el-icon><View /></el-icon>语义图谱</el-button>
        <el-button type="primary"><el-icon><Upload /></el-icon>发布语义包</el-button>
      </template>
    </PageHeader>

    <div class="semantic-kpis page-section">
      <span><strong>3</strong>示例语义对象<small>仅验证治理信息结构</small></span>
      <span><strong>3</strong>来源绑定<small>不包含真实领域数据</small></span>
      <span><strong>0</strong>生产消费方<small>V1.1 前不启用</small></span>
      <span><strong>33%</strong>评审完成率<small>1 / 3 达到 reviewed</small></span>
      <span class="semantic-kpis__health"><i />后置能力<small>不阻塞 M0 至 M4</small></span>
    </div>

    <section class="surface-panel semantic-workbench page-section">
      <div v-if="activeTab === 'objects'" class="object-workbench">
        <aside class="object-list">
          <div class="domain-label"><span>中性示例包</span><small>example@0.1.0</small></div>
          <button v-for="item in filteredObjects" :key="item.id" type="button" :class="{ active: selectedId === item.id }" @click="selectedId = item.id">
            <span class="object-glyph"><el-icon><Box /></el-icon></span>
            <span><strong>{{ item.name }}</strong><small class="mono">{{ item.id }}</small></span>
            <StatusTag :status="item.status" />
          </button>
        </aside>

        <main class="object-detail">
          <header class="object-detail__header">
            <div>
              <div><span class="object-type">OBJECT</span><StatusTag :status="selectedObject.status" /></div>
              <h2>{{ selectedObject.name }}</h2>
              <code>{{ selectedObject.id }}</code>
            </div>
            <div class="object-detail__actions"><el-button>查看 YAML</el-button><el-button type="primary" plain>编辑草稿</el-button></div>
          </header>

          <div class="object-summary">
            <dl>
              <div><dt>数据拥有应用</dt><dd>{{ selectedObject.owner }}</dd></div>
              <div><dt>领域命名空间</dt><dd>{{ selectedObject.domain }}</dd></div>
              <div><dt>当前版本</dt><dd>{{ selectedObject.version }}</dd></div>
              <div><dt>数据负责人</dt><dd>平台数据负责人</dd></div>
            </dl>
            <div class="object-counts"><span><strong>{{ selectedObject.properties }}</strong>属性</span><span><strong>{{ selectedObject.relations }}</strong>关系</span><span><strong>{{ selectedObject.behaviors }}</strong>行为</span></div>
          </div>

          <section class="object-section">
            <div class="object-section__title"><h3>核心属性</h3><el-button text>查看全部 {{ selectedObject.properties }} 个</el-button></div>
            <el-table :data="objectProperties" size="small">
              <el-table-column prop="name" label="属性标识" min-width="150"><template #default="scope"><span class="mono property-name">{{ scope.row.name }}</span></template></el-table-column>
              <el-table-column prop="label" label="业务名称" min-width="130" />
              <el-table-column prop="type" label="类型" width="105"><template #default="scope"><el-tag size="small" type="info" effect="plain">{{ scope.row.type }}</el-tag></template></el-table-column>
              <el-table-column label="必填" width="80"><template #default="scope"><el-icon :class="scope.row.required ? 'yes' : 'no'"><Check v-if="scope.row.required" /><Minus v-else /></el-icon></template></el-table-column>
              <el-table-column label="数据分类" width="100"><template #default="scope"><span>{{ scope.row.sensitive ? '内部敏感' : '内部' }}</span></template></el-table-column>
            </el-table>
          </section>

          <section class="object-section relation-preview">
            <div class="object-section__title"><h3>业务关系</h3><span>实例关系只读，可追溯来源</span></div>
            <div class="relation-row">
              <div class="relation-node active"><small>example</small><strong>示例记录</strong></div>
              <div class="relation-edge"><span>registered_by</span><i /><em>N : 1</em></div>
              <div class="relation-node"><small>platform</small><strong>应用登记</strong></div>
              <div class="relation-edge"><span>projects_to</span><i /><em>1 : N</em></div>
              <div class="relation-node"><small>platform</small><strong>只读投影</strong></div>
            </div>
          </section>
        </main>
      </div>

      <div v-else-if="activeTab === 'bindings'" class="bindings-view">
        <el-table :data="bindings">
          <el-table-column prop="object" label="语义对象" min-width="210"><template #default="scope"><span class="mono binding-object">{{ scope.row.object }}</span></template></el-table-column>
          <el-table-column prop="application" label="数据拥有应用" width="140" />
          <el-table-column prop="contract" label="公开契约 / 只读投影" min-width="270"><template #default="scope"><code>{{ scope.row.contract }}</code></template></el-table-column>
          <el-table-column prop="version" label="映射版本" width="95" />
          <el-table-column label="验证状态" width="110"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column>
          <el-table-column prop="freshness" label="新鲜度" width="100" />
          <el-table-column label="操作" width="80"><template #default><el-button link type="primary">查看</el-button></template></el-table-column>
        </el-table>
      </div>

      <el-empty v-else description="该模型将在对应迭代启用，原型先验证信息架构与发布入口" />
    </section>
  </div>
</template>

<style scoped>
.semantic-kpis { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); border: 1px solid var(--line); border-radius: 7px; background: #fff; }
.semantic-kpis > span { display: grid; gap: 3px; padding: 12px 16px; border-left: 1px solid #edf0f2; color: var(--ink-500); font-size: 11px; }
.semantic-kpis > span:first-child { border-left: 0; }
.semantic-kpis strong { color: var(--ink-900); font-size: 17px; }
.semantic-kpis small { color: #8b969d; font-size: 10px; }
.semantic-kpis__health { place-content: center; color: #42735e !important; }
.semantic-kpis__health i { display: inline-block; width: 6px; height: 6px; margin-right: 4px; border-radius: 50%; background: #43906c; }
.semantic-workbench { min-height: 540px; overflow: hidden; }
.workbench-tabs { display: flex; flex: 1 1 100%; width: 100%; min-width: 0; align-items: center; flex-wrap: wrap; gap: 0 24px; overflow: visible; }
.workbench-tabs button { position: relative; flex: none; height: 46px; padding: 0 1px; border: 0; color: var(--ink-500); background: transparent; font-size: 16px; cursor: pointer; }
.workbench-tabs button.active { color: var(--ink-900); font-weight: 650; }
.workbench-tabs button.active::after { position: absolute; right: 0; bottom: 0; left: 0; height: 2px; background: var(--accent-500); content: ""; }
.object-workbench { display: grid; grid-template-columns: 275px minmax(0, 1fr); min-height: 550px; }
.object-list { border-right: 1px solid var(--line); background: #fafbfb; }
.domain-label { display: flex; justify-content: space-between; padding: 11px 14px 6px; color: var(--ink-500); font-size: 11px; font-weight: 700; }
.domain-label small { font-weight: 500; }
.object-list > button { display: grid; grid-template-columns: 32px minmax(0, 1fr) auto; align-items: center; width: calc(100% - 14px); gap: 8px; margin: 3px 7px; padding: 9px; border: 0; border-radius: 6px; color: var(--ink-500); background: transparent; text-align: left; cursor: pointer; }
.object-list > button:hover { background: #f0f3f4; }
.object-list > button.active { background: #e9eef0; box-shadow: inset 3px 0 #4c7488; }
.object-glyph { display: grid; width: 30px; height: 30px; border: 1px solid #dde4e7; border-radius: 5px; color: #557688; background: #fff; place-items: center; }
.object-list button > span:nth-child(2) { display: grid; min-width: 0; gap: 3px; }
.object-list button strong { color: var(--ink-900); font-size: 13px; }
.object-list button small { overflow: hidden; color: var(--ink-500); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.object-detail { min-width: 0; padding: 21px; }
.object-detail__header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding-bottom: 17px; border-bottom: 1px solid #e8ecee; }
.object-detail__header > div:first-child > div { display: flex; align-items: center; gap: 7px; }
.object-type { color: #4c7488; font-size: 10px; font-weight: 750; letter-spacing: 0.1em; }
.object-detail__header h2 { margin: 7px 0 2px; color: var(--ink-900); font-size: 19px; }
.object-detail__header code { color: var(--ink-500); font-size: 11px; }
.object-detail__actions { display: flex; gap: 8px; }
.object-summary { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 15px; padding: 16px 0; border-bottom: 1px solid #e8ecee; }
.object-summary dl { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 0; }
.object-summary dt { color: var(--ink-500); font-size: 10px; }
.object-summary dd { margin: 4px 0 0; color: var(--ink-900); font-size: 12px; font-weight: 600; }
.object-counts { display: flex; gap: 9px; }
.object-counts span { display: grid; min-width: 54px; gap: 2px; padding: 7px 9px; border-radius: 5px; color: var(--ink-500); background: #f3f5f6; font-size: 10px; text-align: center; }
.object-counts strong { color: var(--ink-900); font-size: 13px; }
.object-section { margin-top: 18px; }
.object-section__title { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 9px; }
.object-section__title h3 { margin: 0; color: var(--ink-900); font-size: 14px; }
.object-section__title > span { color: var(--ink-500); font-size: 10px; }
.property-name, .binding-object { color: #416c83; font-size: 10px; }
.yes { color: #3d8365; }
.no { color: #a2abb1; }
.relation-preview { padding-top: 16px; border-top: 1px solid #e8ecee; }
.relation-row { display: grid; grid-template-columns: minmax(90px, 1fr) minmax(100px, 1fr) minmax(90px, 1fr) minmax(100px, 1fr) minmax(90px, 1fr); align-items: center; gap: 5px; }
.relation-node { display: grid; gap: 3px; padding: 9px; border: 1px solid #dfe5e8; border-radius: 5px; background: #fafbfb; text-align: center; }
.relation-node.active { border-color: #88a8b6; background: #f0f6f8; }
.relation-node small { color: var(--ink-500); font-size: 10px; }
.relation-node strong { color: var(--ink-900); font-size: 12px; }
.relation-edge { position: relative; display: grid; place-items: center; gap: 3px; color: #798991; font-size: 10px; text-align: center; }
.relation-edge span, .relation-edge em { position: relative; z-index: 1; padding: 1px 4px; background: #fff; font-style: normal; }
.relation-edge i { position: absolute; right: 0; left: 0; height: 1px; background: #bfcbd0; }
.bindings-view { min-height: 500px; }
.bindings-view code { color: #5c6e79; font-size: 11px; }

@media (max-width: 1050px) {
  .semantic-kpis { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .object-workbench { grid-template-columns: 235px minmax(0, 1fr); }
}

@media (max-width: 750px) {
  .semantic-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .object-workbench { grid-template-columns: 1fr; }
  .object-list { max-height: 300px; border-right: 0; border-bottom: 1px solid var(--line); overflow-y: auto; }
  .object-detail__header, .object-summary { align-items: stretch; flex-direction: column; display: flex; }
  .object-summary dl { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .relation-row { grid-template-columns: 1fr; }
  .relation-edge { min-height: 32px; }
  .relation-edge i { top: 0; bottom: 0; left: 50%; width: 1px; height: auto; }
}
</style>
