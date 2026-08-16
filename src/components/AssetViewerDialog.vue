<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import * as yaml from 'js-yaml'
import { fetchAssetText } from '../services/platformApi'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  asset: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue'])

const loading = ref(false)
const error = ref(null)
const rawText = ref('')

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const METHODS = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options']

const parsed = computed(() => {
  if (!props.asset || !rawText.value) return null
  const kind = props.asset.kind
  try {
    if (kind === 'GUIDE') {
      const html = DOMPurify.sanitize(marked.parse(rawText.value), { USE_PROFILES: { html: true } })
      return { type: 'markdown', html }
    }
    if (kind === 'OPENAPI' || kind === 'JSON_SCHEMA') {
      const doc = yaml.load(rawText.value)
      if (kind === 'OPENAPI') return { type: 'openapi', endpoints: extractOpenapi(doc) }
      return { type: 'schema', doc }
    }
    return { type: 'code', language: codeLanguage(props.asset), text: rawText.value }
  } catch (caught) {
    return { type: 'code', language: 'text', text: rawText.value }
  }
})

function codeLanguage(asset) {
  const media = asset.media_type || ''
  if (media.includes('python')) return 'python'
  if (media.includes('json')) return 'json'
  if (media.includes('yaml')) return 'yaml'
  if (media.includes('markdown')) return 'markdown'
  return 'text'
}

function extractOpenapi(doc) {
  const paths = doc?.paths || {}
  const endpoints = []
  Object.entries(paths).forEach(([path, operations]) => {
    METHODS.forEach((method) => {
      const operation = operations?.[method]
      if (!operation) return
      endpoints.push({
        path,
        method: method.toUpperCase(),
        summary: operation.summary || operation.operationId || '',
        description: operation.description || '',
        tags: operation.tags || [],
        parameters: (operation.parameters || []).map((parameter) => ({
          name: parameter.name,
          in: parameter.in,
          required: Boolean(parameter.required),
          description: parameter.description || '',
        })),
        responses: Object.keys(operation.responses || {}),
      })
    })
  })
  return endpoints
}

function methodTagType(method) {
  return { GET: 'success', POST: 'primary', PUT: 'warning', PATCH: 'warning', DELETE: 'danger' }[method] || 'info'
}

async function load() {
  if (!props.asset) return
  loading.value = true
  error.value = null
  rawText.value = ''
  try {
    rawText.value = await fetchAssetText(props.asset.download_path)
  } catch (caught) {
    error.value = caught
    ElMessage.error(caught.message || '加载资产内容失败')
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.modelValue, props.asset?.asset_id],
  ([isVisible]) => {
    if (isVisible) load()
  },
)
</script>

<template>
  <el-dialog
    v-model="visible"
    width="80%"
    top="4vh"
    class="asset-viewer-dialog"
    :title="asset ? `${asset.title}（v${asset.version}）` : '资产预览'"
    destroy-on-close
  >
    <div v-loading="loading" class="asset-viewer-body">
      <el-alert
        v-if="error"
        type="error"
        :title="error.message || '资产内容加载失败'"
        :closable="false"
        show-icon
      />
      <template v-else-if="parsed">
        <div v-if="parsed.type === 'markdown'" class="markdown-body" v-html="parsed.html" />

        <div v-else-if="parsed.type === 'openapi'" class="openapi-body">
          <p class="endpoint-count">共 {{ parsed.endpoints.length }} 个端点</p>
          <el-collapse>
            <el-collapse-item v-for="(endpoint, index) in parsed.endpoints" :key="index">
              <template #title>
                <div class="endpoint-title">
                  <el-tag :type="methodTagType(endpoint.method)" effect="dark" size="small" class="method-tag">
                    {{ endpoint.method }}
                  </el-tag>
                  <code class="endpoint-path">{{ endpoint.path }}</code>
                  <span class="endpoint-summary">{{ endpoint.summary }}</span>
                </div>
              </template>
              <div class="endpoint-detail">
                <p v-if="endpoint.description" class="endpoint-description">{{ endpoint.description }}</p>
                <div v-if="endpoint.tags.length" class="endpoint-row">
                  <span class="endpoint-label">标签</span>
                  <el-tag v-for="tag in endpoint.tags" :key="tag" size="small" effect="plain">{{ tag }}</el-tag>
                </div>
                <div v-if="endpoint.parameters.length" class="endpoint-params">
                  <span class="endpoint-label">参数</span>
                  <el-table :data="endpoint.parameters" size="small">
                    <el-table-column prop="name" label="名称" min-width="140" />
                    <el-table-column prop="in" label="位置" width="90" />
                    <el-table-column label="必填" width="70">
                      <template #default="scope">{{ scope.row.required ? '是' : '否' }}</template>
                    </el-table-column>
                    <el-table-column prop="description" label="说明" min-width="200" />
                  </el-table>
                </div>
                <div class="endpoint-row">
                  <span class="endpoint-label">响应码</span>
                  <code v-for="code in endpoint.responses" :key="code" class="response-code">{{ code }}</code>
                </div>
              </div>
            </el-collapse-item>
          </el-collapse>
        </div>

        <div v-else-if="parsed.type === 'schema'" class="schema-body">
          <pre class="schema-json">{{ JSON.stringify(parsed.doc, null, 2) }}</pre>
        </div>

        <pre v-else class="code-body" :data-language="parsed.language"><code>{{ parsed.text }}</code></pre>
      </template>
    </div>
    <template #footer>
      <span class="viewer-footer-hint">内容与下载文件同源、同版本，可通过 SHA-256 校验完整性。</span>
    </template>
  </el-dialog>
</template>

<style scoped>
.asset-viewer-body {
  min-height: 200px;
  max-height: 70vh;
  overflow: auto;
}
.endpoint-count {
  margin: 0 0 10px;
  color: var(--ink-500);
  font-size: 12px;
}
.endpoint-title {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}
.method-tag {
  min-width: 58px;
  text-align: center;
}
.endpoint-path {
  font-weight: 600;
  color: var(--ink-900);
}
.endpoint-summary {
  color: var(--ink-500);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.endpoint-detail {
  display: grid;
  gap: 12px;
}
.endpoint-description {
  margin: 0;
  color: var(--ink-700);
  font-size: 13px;
}
.endpoint-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.endpoint-label {
  color: var(--ink-500);
  font-size: 12px;
  min-width: 44px;
}
.endpoint-params {
  display: grid;
  gap: 6px;
}
.response-code {
  padding: 2px 8px;
  background: var(--surface-200, #f2f4f8);
  border-radius: 4px;
  font-size: 12px;
}
.schema-json,
.code-body {
  margin: 0;
  padding: 16px;
  background: var(--ink-900, #1c2333);
  color: #e6e9f0;
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.6;
  overflow: auto;
  white-space: pre;
}
.viewer-footer-hint {
  color: var(--ink-500);
  font-size: 12px;
}

.markdown-body {
  line-height: 1.7;
  color: var(--ink-900);
  font-size: 14px;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  margin: 1.2em 0 0.5em;
  line-height: 1.3;
}
.markdown-body :deep(h1) {
  font-size: 22px;
  border-bottom: 1px solid var(--surface-300, #e2e6ee);
  padding-bottom: 8px;
}
.markdown-body :deep(h2) {
  font-size: 18px;
  border-bottom: 1px solid var(--surface-300, #e2e6ee);
  padding-bottom: 6px;
}
.markdown-body :deep(h3) {
  font-size: 16px;
}
.markdown-body :deep(p) {
  margin: 0.6em 0;
}
.markdown-body :deep(code) {
  background: var(--surface-200, #f2f4f8);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13px;
}
.markdown-body :deep(pre) {
  background: var(--ink-900, #1c2333);
  color: #e6e9f0;
  padding: 14px;
  border-radius: 8px;
  overflow: auto;
}
.markdown-body :deep(pre code) {
  background: none;
  padding: 0;
  color: inherit;
}
.markdown-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.8em 0;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--surface-300, #e2e6ee);
  padding: 6px 12px;
  text-align: left;
}
.markdown-body :deep(th) {
  background: var(--surface-100, #f7f8fb);
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 1.4em;
}
.markdown-body :deep(blockquote) {
  margin: 0.8em 0;
  padding: 4px 14px;
  border-left: 3px solid var(--accent, #3d6df5);
  color: var(--ink-500);
  background: var(--surface-100, #f7f8fb);
}
.markdown-body :deep(a) {
  color: var(--accent, #3d6df5);
}
</style>
