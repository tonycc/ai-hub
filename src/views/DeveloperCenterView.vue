<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import ApiState from '../components/ApiState.vue'
import PageHeader from '../components/PageHeader.vue'
import { apiRequest, downloadAsset } from '../services/platformApi'

const loading = ref(false)
const error = ref(null)
const catalog = ref(null)
const sandbox = ref(null)

function formatBytes(value) { return value < 1024 ? `${value} B` : `${(value / 1024).toFixed(1)} KiB` }
async function load() {
  loading.value = true; error.value = null
  try { [catalog.value, sandbox.value] = await Promise.all([apiRequest('developer/catalog'), apiRequest('developer/sandbox')]) }
  catch (caught) { error.value = caught } finally { loading.value = false }
}
async function download(item) {
  try { await downloadAsset(item.download_path, item.asset_id); ElMessage.success(`${item.title} 已下载`) }
  catch (caught) { ElMessage.error(caught.message) }
}
async function copy(value) { await navigator.clipboard.writeText(value); ElMessage.success('已复制') }
onMounted(load)
</script>

<template><div class="page-shell"><PageHeader title="开发者中心" description="获取版本化契约、Python 示例、沙箱参数与独立应用接入指南；API-only 始终是默认档位。"><template #actions><el-button @click="load"><el-icon><Refresh/></el-icon>校验目录</el-button><el-button type="primary" @click="$router.push('/platform/integrations')"><el-icon><CircleCheck/></el-icon>运行接入认证</el-button></template></PageHeader><ApiState :loading="loading" :error="error" :empty="!catalog" @retry="load"><template v-if="catalog"><section class="developer-summary page-section"><article class="surface-panel"><span>目录版本</span><strong>{{catalog.catalog_version}}</strong><small class="mono">{{catalog.catalog_sha256}}</small></article><article class="surface-panel"><span>公开制品</span><strong>{{catalog.total}}</strong><small>均含 SHA-256 摘要</small></article><article class="surface-panel"><span>默认能力</span><strong>API_CLIENT</strong><small>不要求 RabbitMQ、Outbox 或 Inbox</small></article></section><section class="surface-panel page-section catalog-panel"><div class="section-heading panel-heading"><div><h2>版本化公开资产</h2><p>下载路径来自固定白名单，不能读取任意服务器文件。</p></div></div><el-table :data="catalog.items" style="width:100%"><el-table-column prop="title" label="资产" min-width="240"><template #default="scope"><strong>{{scope.row.title}}</strong><small class="subline mono">{{scope.row.asset_id}}</small></template></el-table-column><el-table-column prop="kind" label="类型" width="130"><template #default="scope"><el-tag effect="plain">{{scope.row.kind}}</el-tag></template></el-table-column><el-table-column prop="version" label="版本" width="100"/><el-table-column prop="required_capability" label="所需能力" min-width="220"><template #default="scope"><code>{{scope.row.required_capability}}</code></template></el-table-column><el-table-column prop="size_bytes" label="大小" width="100"><template #default="scope">{{formatBytes(scope.row.size_bytes)}}</template></el-table-column><el-table-column prop="sha256" label="SHA-256" min-width="230"><template #default="scope"><el-tooltip :content="scope.row.sha256"><code>{{scope.row.sha256.slice(0,16)}}…</code></el-tooltip></template></el-table-column><el-table-column label="操作" width="100" fixed="right"><template #default="scope"><el-button type="primary" link @click="download(scope.row)">下载</el-button></template></el-table-column></el-table></section><section v-if="sandbox" class="surface-panel page-section sandbox-panel"><div class="section-heading"><div><h2>本地沙箱</h2><p>沙箱只提供非敏感参数，客户端密钥必须通过应用中心一次性领取。</p></div><el-tag type="success" effect="plain">不含密钥</el-tag></div><el-descriptions :column="2" border><el-descriptions-item label="应用编号"><code>{{sandbox.application_id}}</code></el-descriptions-item><el-descriptions-item label="测试主体"><code>{{sandbox.user_subject}}</code></el-descriptions-item><el-descriptions-item label="平台 API"><code>{{sandbox.platform_base_url}}</code><el-button link @click="copy(sandbox.platform_base_url)">复制</el-button></el-descriptions-item><el-descriptions-item label="OIDC Issuer"><code>{{sandbox.oidc_issuer}}</code><el-button link @click="copy(sandbox.oidc_issuer)">复制</el-button></el-descriptions-item><el-descriptions-item label="Discovery"><code>{{sandbox.oidc_discovery_url}}</code></el-descriptions-item><el-descriptions-item label="Audience"><code>{{sandbox.oidc_audience}}</code></el-descriptions-item><el-descriptions-item label="默认能力"><el-tag v-for="item in sandbox.default_capabilities" :key="item" size="small">{{item}}</el-tag></el-descriptions-item><el-descriptions-item label="可选能力"><el-tag v-for="item in sandbox.optional_capabilities" :key="item" size="small" effect="plain" class="inline-tag">{{item}}</el-tag></el-descriptions-item></el-descriptions></section></template></ApiState></div></template>

<style scoped>.developer-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.developer-summary article{display:grid;gap:7px;padding:18px}.developer-summary span,.developer-summary small{color:var(--ink-500);font-size:11px}.developer-summary strong{color:var(--ink-900);font-size:21px}.developer-summary small.mono{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.catalog-panel,.sandbox-panel{padding:18px;overflow:hidden}.panel-heading{margin-bottom:12px}.subline{display:block;margin-top:4px;color:var(--ink-500);font-size:11px}.inline-tag{margin:2px 4px 2px 0}@media(max-width:800px){.developer-summary{grid-template-columns:1fr}}</style>
