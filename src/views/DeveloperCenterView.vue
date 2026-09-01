<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import ApiState from '../components/ApiState.vue'
import AssetViewerDialog from '../components/AssetViewerDialog.vue'
import PageHeader from '../components/PageHeader.vue'
import { apiRequest, downloadAsset } from '../services/platformApi'

const ASSET_LABELS = {
  'agent-integration': 'Agent 接入索引',
  'integration-guide': '独立应用接入指南',
  'platform-openapi': 'Platform API OpenAPI',
  'api-only-python': 'API-only 快速入门',
  'data-read-python': '汇聚数据读取',
  'data-ingest-evidence': 'DATA_INGEST 认证证据',
}

const ASSET_SCENARIOS = {
  'agent-integration': 'Coding agent 固定阅读顺序与检查清单',
  'api-only-python': '首次接入：登录、权限、通知',
  'data-read-python': '读取平台汇聚的业务数据',
  'data-ingest-evidence': '启用 DATA_INGEST 后提交认证证据',
}

const loading = ref(false)
const error = ref(null)
const catalog = ref(null)
const sandbox = ref(null)
const viewerVisible = ref(false)
const viewerAsset = ref(null)

const KIND_LABELS = {
  GUIDE: '接入指南',
  OPENAPI: 'API 契约',
  JSON_SCHEMA: '数据结构',
  SDK_EXAMPLE: '示例代码',
}

const resources = computed(() => {
  if (!catalog.value) return []
  const order = { GUIDE: 0, SDK_EXAMPLE: 1, OPENAPI: 2, JSON_SCHEMA: 3 }
  return [...catalog.value.items]
    .sort((a, b) => (order[a.kind] ?? 99) - (order[b.kind] ?? 99))
    .map((item) => ({
      ...item,
      kindLabel: KIND_LABELS[item.kind] || item.kind,
      displayName: ASSET_LABELS[item.asset_id] || item.title,
      scenario: ASSET_SCENARIOS[item.asset_id] || null,
    }))
})

const envSnippet = computed(() => {
  if (!sandbox.value?.available) return ''
  const s = sandbox.value
  return [
    `PLATFORM_BASE_URL=${s.platform_base_url}`,
    `OIDC_ISSUER=${s.oidc_issuer}`,
    `OIDC_AUDIENCE=${s.oidc_audience}`,
    `APPLICATION_ID=${s.application_id}`,
    `CLIENT_SECRET=在应用中心一次性领取`,
  ].join('\n')
})

async function load() {
  loading.value = true
  error.value = null
  try {
    ;[catalog.value, sandbox.value] = await Promise.all([
      apiRequest('developer/catalog'),
      apiRequest('developer/sandbox'),
    ])
  } catch (caught) {
    error.value = caught
  } finally {
    loading.value = false
  }
}

async function download(item) {
  try {
    await downloadAsset(item.download_path, item.asset_id)
    ElMessage.success(`${item.title} 已下载`)
  } catch (caught) {
    ElMessage.error(caught.message)
  }
}

function preview(item) {
  viewerAsset.value = item
  viewerVisible.value = true
}

async function copy(value, label = '已复制') {
  try {
    await navigator.clipboard.writeText(value)
    ElMessage.success(label)
  } catch {
    ElMessage.error('复制失败，请手动选择复制')
  }
}

onMounted(load)
</script>

<template>
  <div class="page-shell">
    <PageHeader
      title="开发者中心"
      description="接入文档、契约与本地沙箱参数。默认从 API-only 开始；需要平台拉取业务数据时再启用 DATA_INGEST。"
    />

    <ApiState :loading="loading" :error="error" :empty="!catalog" @retry="load">
      <template v-if="catalog">
        <section class="surface-panel page-section resource-panel">
          <div class="section-heading">
            <h2>接入资源</h2>
            <p>每个文件带版本与 SHA-256，可在线查看或下载。</p>
          </div>
          <el-table :data="resources" style="width: 100%">
            <el-table-column prop="kindLabel" label="类型" width="100" />
            <el-table-column label="名称" min-width="180">
              <template #default="scope">
                <strong>{{ scope.row.displayName }}</strong>
                <small v-if="scope.row.scenario" class="subline">{{ scope.row.scenario }}</small>
              </template>
            </el-table-column>
            <el-table-column prop="version" label="版本" width="90">
              <template #default="scope">v{{ scope.row.version }}</template>
            </el-table-column>
            <el-table-column label="操作" width="160" fixed="right">
              <template #default="scope">
                <el-button type="primary" link @click="preview(scope.row)">查看</el-button>
                <el-button link @click="download(scope.row)">下载</el-button>
              </template>
            </el-table-column>
          </el-table>
        </section>

        <section v-if="sandbox?.available" class="surface-panel page-section sandbox-panel">
          <div class="section-heading">
            <h2>本地沙箱</h2>
            <p>非敏感参数可直接写入 <code>.env</code>；客户端密钥请到应用中心创建环境凭据后一次性保存。</p>
          </div>
          <div class="sandbox-toolbar">
            <el-button size="small" plain @click="copy(envSnippet, '环境变量已复制')">复制环境变量</el-button>
            <span v-if="sandbox.default_capabilities?.length" class="sandbox-caps">
              默认能力
              <el-tag v-for="c in sandbox.default_capabilities" :key="c" size="small">{{ c }}</el-tag>
            </span>
          </div>
          <pre class="env-snippet">{{ envSnippet }}</pre>
        </section>
        <el-alert
          v-else-if="sandbox"
          class="page-section"
          type="info"
          :closable="false"
          show-icon
          title="当前部署未启用参考沙箱"
          :description="sandbox.message"
        />

        <section class="page-section">
          <el-collapse class="meta-collapse">
            <el-collapse-item name="integrity">
              <template #title>
                <span class="meta-collapse__title">版本与指纹（CI / 工具链）</span>
              </template>
              <div class="meta-collapse__body">
                <el-descriptions :column="1" border size="small">
                  <el-descriptions-item label="目录版本">{{ catalog.catalog_version }}</el-descriptions-item>
                  <el-descriptions-item label="目录 SHA-256">
                    <code class="mono">{{ catalog.catalog_sha256 }}</code>
                    <el-button link size="small" @click="copy(catalog.catalog_sha256, '指纹已复制')">复制</el-button>
                  </el-descriptions-item>
                </el-descriptions>
                <el-table :data="catalog.items" size="small" class="meta-table">
                  <el-table-column prop="title" label="资产" min-width="180" />
                  <el-table-column prop="version" label="版本" width="80" />
                  <el-table-column label="SHA-256" min-width="200">
                    <template #default="scope">
                      <el-tooltip :content="scope.row.sha256">
                        <code class="mono">{{ scope.row.sha256.slice(0, 16) }}…</code>
                      </el-tooltip>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
            </el-collapse-item>
          </el-collapse>
        </section>
      </template>
    </ApiState>

    <AssetViewerDialog v-model="viewerVisible" :asset="viewerAsset" />
  </div>
</template>

<style scoped>
.section-heading {
  margin-bottom: var(--space-gap);
}
.section-heading h2 {
  margin: 0 0 4px;
  font-size: var(--font-heading);
  color: var(--ink-900);
}
.section-heading p {
  margin: 0;
  font-size: var(--font-body);
  color: var(--ink-500);
}
.section-heading code {
  background: var(--surface-soft);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: var(--font-caption);
}

.resource-panel,
.sandbox-panel {
  padding: var(--space-card-lg);
  overflow: hidden;
}

.sandbox-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-gap);
  margin-bottom: var(--space-gap);
}
.sandbox-caps {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: var(--font-caption);
  color: var(--ink-500);
}

.env-snippet {
  margin: 0;
  padding: var(--space-card);
  background: var(--ink-900);
  color: #e6e9f0;
  border-radius: 8px;
  font-size: var(--font-caption);
  line-height: 1.8;
  overflow: auto;
  white-space: pre;
}

.meta-collapse {
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface);
  padding: 0 var(--space-card-lg);
}
.meta-collapse :deep(.el-collapse-item__header) { border-bottom: none; }
.meta-collapse :deep(.el-collapse-item__wrap) { border-top: 1px solid var(--line); }
.meta-collapse__title {
  font-size: var(--font-body);
  color: var(--ink-700);
}
.meta-collapse__body {
  padding: 4px 0 var(--space-card-lg);
}
.meta-table {
  margin-top: var(--space-gap);
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: var(--font-caption);
  word-break: break-all;
}
.subline {
  display: block;
  margin-top: 4px;
  color: var(--ink-500);
  font-size: var(--font-caption);
  font-weight: normal;
}
</style>
