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

function resolveRef(doc, ref, seen = new Set()) {
  if (!ref || typeof ref !== 'string' || !ref.startsWith('#/') || seen.has(ref)) return null
  seen.add(ref)
  let node = doc
  for (const part of ref.slice(2).split('/')) {
    node = node?.[part]
    if (node === undefined) return null
  }
  if (node?.$ref) return resolveRef(doc, node.$ref, seen)
  return node
}

function schemaTypeLabel(doc, schema, seen = new Set()) {
  if (!schema) return ''
  if (schema.$ref) {
    if (seen.has(schema.$ref)) return schema.$ref.split('/').pop() || 'object'
    seen.add(schema.$ref)
    const resolved = resolveRef(doc, schema.$ref)
    if (resolved && resolved !== schema) return schemaTypeLabel(doc, resolved, seen)
    return schema.$ref.split('/').pop() || 'object'
  }
  if (schema.type === 'array') {
    const itemType = schemaTypeLabel(doc, schema.items, seen)
    return `array<${itemType || 'any'}>`
  }
  if (schema.enum?.length) return `enum(${schema.enum.join('|')})`
  if (schema.const !== undefined) return `const ${JSON.stringify(schema.const)}`
  if (Array.isArray(schema.type)) {
    const types = schema.type.filter((value) => value !== 'null')
    return types.join('|') + (schema.type.includes('null') ? '?' : '')
  }
  if (schema.type && schema.format) return `${schema.type}(${schema.format})`
  return schema.type || 'object'
}

function flattenSchemaFields(doc, schema, options = {}) {
  const { prefix = '', depth = 0, maxDepth = 5, seen = new Set() } = options
  const fields = []
  if (!schema || depth > maxDepth) return fields

  let resolved = schema
  if (schema.$ref) {
    if (seen.has(schema.$ref)) {
      fields.push({
        name: prefix || schema.$ref.split('/').pop(),
        type: schema.$ref.split('/').pop(),
        required: false,
        description: '',
      })
      return fields
    }
    seen.add(schema.$ref)
    resolved = resolveRef(doc, schema.$ref)
    if (!resolved) return fields
  }

  if (resolved.type === 'array' && resolved.items) {
    const itemSchema = resolved.items.$ref ? resolveRef(doc, resolved.items.$ref) : resolved.items
    if (itemSchema?.properties) {
      const reqSet = new Set(itemSchema.required || [])
      Object.entries(itemSchema.properties).forEach(([key, propSchema]) => {
        const fieldName = prefix ? `${prefix}[].${key}` : `[].${key}`
        fields.push({
          name: fieldName,
          type: schemaTypeLabel(doc, propSchema),
          required: reqSet.has(key),
          description: (propSchema.$ref ? resolveRef(doc, propSchema.$ref) : propSchema)?.description || '',
        })
      })
      return fields
    }
    if (prefix) {
      fields.push({
        name: prefix,
        type: schemaTypeLabel(doc, resolved),
        required: false,
        description: resolved.description || '',
      })
    }
    return fields
  }

  if (resolved.properties) {
    const reqSet = new Set(resolved.required || [])
    Object.entries(resolved.properties).forEach(([key, propSchema]) => {
      const fieldName = prefix ? `${prefix}.${key}` : key
      const propResolved = propSchema.$ref ? resolveRef(doc, propSchema.$ref) : propSchema
      const nestedArrayObject =
        propResolved?.type === 'array' &&
        (propResolved.items?.properties || propResolved.items?.$ref)

      if (propResolved?.properties || nestedArrayObject) {
        fields.push(...flattenSchemaFields(doc, propSchema, { prefix: fieldName, depth: depth + 1, maxDepth, seen: new Set(seen) }))
      } else {
        fields.push({
          name: fieldName,
          type: schemaTypeLabel(doc, propSchema),
          required: reqSet.has(key),
          description: propResolved?.description || propSchema.description || '',
        })
      }
    })
    return fields
  }

  if (prefix) {
    fields.push({
      name: prefix,
      type: schemaTypeLabel(doc, resolved),
      required: false,
      description: resolved.description || '',
    })
  }
  return fields
}

function resolveParameter(doc, parameter) {
  const resolved = parameter.$ref ? resolveRef(doc, parameter.$ref) : parameter
  if (!resolved) return null
  const schema = resolved.schema?.$ref ? resolveRef(doc, resolved.schema.$ref) : resolved.schema
  return {
    name: resolved.name,
    in: resolved.in,
    required: Boolean(resolved.required),
    type: schemaTypeLabel(doc, schema),
    description: resolved.description || '',
  }
}

function extractRequestBody(doc, operation) {
  const body = operation.requestBody
  if (!body) return null
  const schema = body.content?.['application/json']?.schema
  return {
    required: Boolean(body.required),
    description: body.description || '',
    fields: schema ? flattenSchemaFields(doc, schema) : [],
  }
}

function extractResponses(doc, operation) {
  return Object.entries(operation.responses || {})
    .map(([status, response]) => {
      const resolved = response.$ref ? resolveRef(doc, response.$ref) : response
      const schema = resolved?.content?.['application/json']?.schema
      return {
        status,
        description: resolved?.description || '',
        fields: schema ? flattenSchemaFields(doc, schema) : [],
      }
    })
    .sort((a, b) => {
      const na = Number(a.status)
      const nb = Number(b.status)
      if (!Number.isNaN(na) && !Number.isNaN(nb)) return na - nb
      return a.status.localeCompare(b.status)
    })
}

function extractOpenapi(doc) {
  const paths = doc?.paths || {}
  const endpoints = []
  Object.entries(paths).forEach(([path, operations]) => {
    METHODS.forEach((method) => {
      const operation = operations?.[method]
      if (!operation) return
      const responses = extractResponses(doc, operation)
      const successResponse = responses.find((response) => response.status.startsWith('2'))
      endpoints.push({
        path,
        method: method.toUpperCase(),
        summary: operation.summary || successResponse?.description || operation.operationId || '',
        description: operation.description || '',
        tags: operation.tags || [],
        parameters: (operation.parameters || []).map((parameter) => resolveParameter(doc, parameter)).filter(Boolean),
        requestBody: extractRequestBody(doc, operation),
        responses,
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
  <el-drawer
    v-model="visible"
    size="min(80%, 96vw)"
    class="asset-viewer-drawer"
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
                    <el-table-column prop="type" label="类型" min-width="120" />
                    <el-table-column label="必填" width="70">
                      <template #default="scope">{{ scope.row.required ? '是' : '否' }}</template>
                    </el-table-column>
                    <el-table-column prop="description" label="说明" min-width="200" />
                  </el-table>
                </div>
                <div v-if="endpoint.requestBody" class="endpoint-params">
                  <span class="endpoint-label">请求体</span>
                  <p v-if="endpoint.requestBody.description" class="schema-note">{{ endpoint.requestBody.description }}</p>
                  <p class="schema-note">
                    {{ endpoint.requestBody.required ? '必填' : '可选' }} · application/json
                  </p>
                  <el-table v-if="endpoint.requestBody.fields.length" :data="endpoint.requestBody.fields" size="small">
                    <el-table-column prop="name" label="字段" min-width="160" />
                    <el-table-column prop="type" label="类型" min-width="120" />
                    <el-table-column label="必填" width="70">
                      <template #default="scope">{{ scope.row.required ? '是' : '否' }}</template>
                    </el-table-column>
                    <el-table-column prop="description" label="说明" min-width="200" />
                  </el-table>
                </div>
                <div v-if="endpoint.responses.length" class="endpoint-responses">
                  <span class="endpoint-label">响应</span>
                  <div v-for="response in endpoint.responses" :key="response.status" class="response-block">
                    <div class="response-header">
                      <code class="response-code">{{ response.status }}</code>
                      <span class="response-description">{{ response.description }}</span>
                    </div>
                    <el-table v-if="response.fields.length" :data="response.fields" size="small">
                      <el-table-column prop="name" label="字段" min-width="160" />
                      <el-table-column prop="type" label="类型" min-width="120" />
                      <el-table-column label="必填" width="70">
                        <template #default="scope">{{ scope.row.required ? '是' : '否' }}</template>
                      </el-table-column>
                      <el-table-column prop="description" label="说明" min-width="200" />
                    </el-table>
                  </div>
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
  </el-drawer>
</template>

<style scoped>
.asset-viewer-body {
  min-height: 200px;
  max-height: 70vh;
  overflow: auto;
}
.endpoint-count {
  margin: 0 0 var(--space-gap);
  color: var(--ink-500);
  font-size: var(--font-caption);
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
  font-size: var(--font-caption);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.endpoint-detail {
  display: grid;
  gap: var(--space-gap);
}
.endpoint-description {
  margin: 0;
  color: var(--ink-700);
  font-size: var(--font-body);
}
.endpoint-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.endpoint-label {
  color: var(--ink-500);
  font-size: var(--font-caption);
  min-width: 44px;
}
.endpoint-params,
.endpoint-responses {
  display: grid;
  gap: var(--space-gap);
}
.schema-note {
  margin: 0;
  color: var(--ink-500);
  font-size: var(--font-caption);
}
.response-block {
  display: grid;
  gap: var(--space-gap);
  padding: var(--space-gap);
  background: var(--surface-100, #f7f8fb);
  border-radius: 6px;
}
.response-header {
  display: flex;
  align-items: baseline;
  gap: var(--space-gap);
  flex-wrap: wrap;
}
.response-code {
  padding: 2px 8px;
  background: var(--surface-200, #f2f4f8);
  border-radius: 4px;
  font-size: var(--font-caption);
}
.response-description {
  color: var(--ink-700);
  font-size: var(--font-body);
}
.schema-json,
.code-body {
  margin: 0;
  padding: 16px;
  background: var(--ink-900, #1c2333);
  color: #e6e9f0;
  border-radius: 8px;
  font-size: var(--font-caption);
  line-height: 1.6;
  overflow: auto;
  white-space: pre;
}
.viewer-footer-hint {
  color: var(--ink-500);
  font-size: var(--font-caption);
}

.markdown-body {
  line-height: 1.7;
  color: var(--ink-900);
  font-size: var(--font-body-lg);
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
