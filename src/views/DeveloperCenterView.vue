<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ArrowRight,
  CircleCheck,
  Document,
  Download,
  Key,
  Notebook,
  View,
} from '@element-plus/icons-vue'
import ApiState from '../components/ApiState.vue'
import AssetViewerDialog from '../components/AssetViewerDialog.vue'
import { apiRequest, downloadAsset } from '../services/platformApi'

const router = useRouter()
const loading = ref(false)
const error = ref(null)
const catalog = ref(null)
const sandbox = ref(null)
const viewerVisible = ref(false)
const viewerAsset = ref(null)

const KIND_META = {
  GUIDE: { label: '接入指南', icon: Notebook, tone: 'guide', blurb: '从零开始：登记应用、拿密钥、验证登录、跑通第一次调用。' },
  OPENAPI: { label: 'API 契约', icon: Document, tone: 'api', blurb: '全部接口的端点、参数与响应模型，可在线浏览或下载给工具链。' },
  JSON_SCHEMA: { label: '数据结构', icon: Document, tone: 'api', blurb: '公开数据结构校验规则，用于本地校验请求或载荷格式。' },
  SDK_EXAMPLE: { label: '示例代码', icon: View, tone: 'example', blurb: '可直接运行的最小 Python 接入示例，照着改即可。' },
}

const resources = computed(() => {
  if (!catalog.value) return []
  const order = { GUIDE: 0, SDK_EXAMPLE: 1, OPENAPI: 2, JSON_SCHEMA: 3 }
  return [...catalog.value.items]
    .sort((a, b) => (order[a.kind] ?? 99) - (order[b.kind] ?? 99))
    .map((item) => ({ ...item, meta: KIND_META[item.kind] || KIND_META.OPENAPI }))
})

const guide = computed(() => resources.value.find((item) => item.kind === 'GUIDE') || null)

const envSnippet = computed(() => {
  if (!sandbox.value) return ''
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
    <header class="hero">
      <div class="hero__copy">
        <h1>开发者中心</h1>
        <p>把一个新应用接入平台：读指南、配环境、跑认证。默认从 API-only 开始；需要平台拉取业务数据时再启用 DATA_INGEST。</p>
      </div>
      <div class="hero__actions">
        <el-button v-if="guide" type="primary" size="large" @click="preview(guide)">
          <el-icon><Notebook /></el-icon>从接入指南开始
        </el-button>
        <el-button size="large" @click="router.push('/platform/integrations')">
          <el-icon><CircleCheck /></el-icon>运行接入认证
        </el-button>
      </div>
    </header>

    <ApiState :loading="loading" :error="error" :empty="!catalog" @retry="load">
      <template v-if="catalog">
        <section class="page-section steps">
          <article class="step surface-panel">
            <span class="step__index">1</span>
            <div class="step__body">
              <h3>读懂接入方式</h3>
              <p>先看接入指南，了解登记应用、领取密钥和验证登录的完整流程。</p>
              <el-button v-if="guide" type="primary" plain size="small" @click="preview(guide)">
                阅读指南<el-icon class="el-icon--right"><ArrowRight /></el-icon>
              </el-button>
            </div>
          </article>
          <article class="step surface-panel">
            <span class="step__index">2</span>
            <div class="step__body">
              <h3>配置本地环境</h3>
              <p>用下方沙箱参数填进 <code>.env</code>，密钥到应用中心一次性领取。</p>
              <el-button plain size="small" @click="copy(envSnippet, '环境变量已复制')">
                复制环境变量
              </el-button>
            </div>
          </article>
          <article class="step surface-panel">
            <span class="step__index">3</span>
            <div class="step__body">
              <h3>验证接入正确</h3>
              <p>联调完成后运行接入认证，确认应用符合平台契约再上线。</p>
              <el-button plain size="small" @click="router.push('/platform/integrations')">
                去认证<el-icon class="el-icon--right"><ArrowRight /></el-icon>
              </el-button>
            </div>
          </article>
        </section>

        <section class="page-section">
          <div class="section-heading">
            <h2>接入资源</h2>
            <p>契约、示例与文档。每个文件都带版本和指纹，可下载给代码生成、Mock 或 CI 校验使用。</p>
          </div>
          <div class="resource-grid">
            <article v-for="item in resources" :key="item.asset_id" class="resource surface-panel">
              <span class="resource__icon" :class="`resource__icon--${item.meta.tone}`">
                <el-icon :size="20"><component :is="item.meta.icon" /></el-icon>
              </span>
              <div class="resource__body">
                <div class="resource__title-row">
                  <h3>{{ item.meta.label }}</h3>
                  <span class="resource__version">v{{ item.version }}</span>
                </div>
                <p class="resource__blurb">{{ item.meta.blurb }}</p>
                <p v-if="item.required_capability !== 'API_CLIENT'" class="resource__prereq">
                  需要能力：{{ item.required_capability }}
                </p>
                <div class="resource__actions">
                  <el-button type="primary" link @click="preview(item)">在线查看</el-button>
                  <el-button link @click="download(item)">
                    <el-icon><Download /></el-icon>下载
                  </el-button>
                </div>
              </div>
            </article>
          </div>
        </section>

        <section v-if="sandbox" class="page-section">
          <div class="section-heading">
            <h2>本地沙箱环境</h2>
            <p>这些是非敏感参数，可直接用于本地联调。客户端密钥不在此处展示。</p>
          </div>
          <div class="sandbox-grid">
            <div class="surface-panel env-panel">
              <div class="env-panel__head">
                <span>环境变量（.env）</span>
                <el-button size="small" plain @click="copy(envSnippet, '环境变量已复制')">一键复制</el-button>
              </div>
              <pre class="env-snippet">{{ envSnippet }}</pre>
            </div>
            <div class="surface-panel secret-panel">
              <div class="secret-panel__icon"><el-icon :size="22"><Key /></el-icon></div>
              <h3>客户端密钥去哪里领？</h3>
              <p>
                密钥属于敏感凭据，需要登记、可审计、可吊销，因此不在此页面展示。
                请到<strong>应用中心</strong>为你的应用创建环境凭据，密钥只会在创建时展示一次，请立即保存。
              </p>
              <el-button type="primary" plain size="small" @click="router.push('/applications')">
                前往应用中心<el-icon class="el-icon--right"><ArrowRight /></el-icon>
              </el-button>
              <div class="secret-panel__caps">
                <span class="caps__label">默认能力</span>
                <el-tag v-for="c in sandbox.default_capabilities" :key="c" size="small">{{ c }}</el-tag>
                <span class="caps__label caps__label--optional">可按需申请</span>
                <el-tag v-for="c in sandbox.optional_capabilities" :key="c" size="small" effect="plain">{{ c }}</el-tag>
              </div>
            </div>
          </div>
        </section>

        <section class="page-section integrity">
          <el-collapse class="integrity__collapse">
            <el-collapse-item name="integrity">
              <template #title>
                <span class="integrity__title">完整性与版本校验（供 CI / 工具链使用）</span>
              </template>
              <div class="integrity__body">
                <div class="integrity__row">
                  <span>契约目录版本</span>
                  <strong>{{ catalog.catalog_version }}</strong>
                </div>
                <div class="integrity__row">
                  <span>目录指纹（SHA-256）</span>
                  <code class="mono">{{ catalog.catalog_sha256 }}</code>
                  <el-button link size="small" @click="copy(catalog.catalog_sha256, '目录指纹已复制')">复制</el-button>
                </div>
                <p class="integrity__hint">
                  锁定某个版本后，可在 CI 中核对此指纹，确认你使用的契约与平台当前发布完全一致、未被悄悄改动。
                </p>
                <el-table :data="catalog.items" size="small">
                  <el-table-column label="资产" min-width="200">
                    <template #default="scope">
                      <span>{{ scope.row.title }}</span>
                      <small class="subline mono">{{ scope.row.asset_id }}</small>
                    </template>
                  </el-table-column>
                  <el-table-column prop="version" label="版本" width="80" />
                  <el-table-column label="大小" width="90">
                    <template #default="scope">{{ (scope.row.size_bytes / 1024).toFixed(1) }} KiB</template>
                  </el-table-column>
                  <el-table-column label="SHA-256" min-width="220">
                    <template #default="scope">
                      <el-tooltip :content="scope.row.sha256">
                        <code class="mono">{{ scope.row.sha256.slice(0, 20) }}…</code>
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
.hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 18px;
  padding: 26px 4px 20px;
}
.hero__copy h1 {
  margin: 0 0 8px;
  font-size: 24px;
  color: var(--ink-900);
}
.hero__copy p {
  margin: 0;
  max-width: 560px;
  font-size: 14px;
  line-height: 1.7;
  color: var(--ink-500);
}
.hero__actions {
  display: flex;
  gap: 12px;
}

.section-heading {
  margin-bottom: 14px;
}
.section-heading h2 {
  margin: 0 0 4px;
  font-size: 17px;
  color: var(--ink-900);
}
.section-heading p {
  margin: 0;
  font-size: 13px;
  color: var(--ink-500);
}

/* steps */
.steps {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}
.step {
  display: flex;
  gap: 14px;
  padding: 20px;
  align-items: flex-start;
}
.step__index {
  flex: none;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--accent-100);
  color: var(--accent-600);
  font-weight: 700;
  display: grid;
  place-items: center;
  font-size: 15px;
}
.step__body h3 {
  margin: 2px 0 6px;
  font-size: 15px;
  color: var(--ink-900);
}
.step__body p {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink-500);
}
.step__body code {
  background: var(--surface-soft);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 12px;
}

/* resources */
.resource-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
}
.resource {
  display: flex;
  gap: 14px;
  padding: 18px;
  align-items: flex-start;
}
.resource__icon {
  flex: none;
  width: 42px;
  height: 42px;
  border-radius: 10px;
  display: grid;
  place-items: center;
}
.resource__icon--guide { background: var(--accent-100); color: var(--accent-600); }
.resource__icon--api { background: var(--surface-soft); color: var(--ink-700); }
.resource__icon--example { background: #e9f6ee; color: var(--success); }
.resource__body { flex: 1; min-width: 0; }
.resource__title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.resource__title-row h3 {
  margin: 0;
  font-size: 15px;
  color: var(--ink-900);
}
.resource__version {
  font-size: 11px;
  color: var(--ink-500);
  background: var(--surface-soft);
  padding: 2px 8px;
  border-radius: 20px;
}
.resource__blurb {
  margin: 6px 0 4px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink-500);
}
.resource__prereq {
  margin: 0 0 4px;
  font-size: 12px;
  color: var(--warning);
}
.resource__actions {
  display: flex;
  gap: 4px;
  margin-top: 8px;
}

/* sandbox */
.sandbox-grid {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 14px;
}
.env-panel, .secret-panel { padding: 18px; }
.env-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  font-size: 13px;
  color: var(--ink-700);
}
.env-snippet {
  margin: 0;
  padding: 14px;
  background: var(--ink-900);
  color: #e6e9f0;
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.8;
  overflow: auto;
  white-space: pre;
}
.secret-panel__icon {
  width: 42px;
  height: 42px;
  border-radius: 10px;
  background: var(--accent-100);
  color: var(--accent-600);
  display: grid;
  place-items: center;
  margin-bottom: 10px;
}
.secret-panel h3 { margin: 0 0 8px; font-size: 15px; color: var(--ink-900); }
.secret-panel p { margin: 0 0 14px; font-size: 13px; line-height: 1.7; color: var(--ink-500); }
.secret-panel__caps {
  margin-top: 16px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.caps__label { font-size: 12px; color: var(--ink-500); margin-right: 2px; }
.caps__label--optional { margin-left: 10px; }

/* integrity */
.integrity__collapse {
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface);
  padding: 0 18px;
}
.integrity__collapse :deep(.el-collapse-item__header) { border-bottom: none; }
.integrity__collapse :deep(.el-collapse-item__wrap) { border-top: 1px solid var(--line); }
.integrity__title { font-size: 13px; color: var(--ink-700); }
.integrity__body { padding: 4px 0 18px; }
.integrity__row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
  font-size: 13px;
}
.integrity__row span { color: var(--ink-500); min-width: 150px; }
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  word-break: break-all;
}
.integrity__hint {
  margin: 0 0 14px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-500);
}
.subline { display: block; margin-top: 2px; color: var(--ink-500); font-size: 11px; }

@media (max-width: 900px) {
  .steps, .sandbox-grid { grid-template-columns: 1fr; }
}
</style>
