<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import ApiState from '../components/ApiState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { usePortalSession } from '../stores/session'
import { ingestListContracts } from '../services/ingestApi'
import {
  semanticDeprecate,
  semanticExportVersion,
  semanticGenerate,
  semanticGetVersion,
  semanticImport,
  semanticListPackages,
  semanticListVersions,
  semanticPublish,
  semanticRetire,
  semanticReview,
  semanticValidate,
  semanticVerify,
  semanticVerifyBinding,
} from '../services/semanticApi'

const session = usePortalSession()
const canWrite = computed(() => session.hasPermission('platform.semantic.write'))
const canReview = computed(() => session.hasPermission('platform.semantic.review'))
const canVerify = computed(() => session.hasPermission('platform.semantic.verify'))
const canPublish = computed(() => session.hasPermission('platform.semantic.publish'))
const canReadContracts = computed(() => session.hasPermission('platform.ingest.read'))

const loading = ref(false)
const error = ref(null)
const packages = ref([])
const selectedPackage = ref(null)
const versions = ref([])
const versionsLoading = ref(false)

const detailVisible = ref(false)
const detail = ref(null)
const detailTab = ref('objects')
const validation = ref(null)
const busy = ref(false)

const lifecycleSteps = ['DRAFT', 'REVIEWED', 'VERIFIED', 'PUBLISHED']
const lifecycleLabels = { DRAFT: '草稿', REVIEWED: '已评审', VERIFIED: '已确认', PUBLISHED: '已发布' }
const originLabels = { GENERATED: '契约生成', IMPORTED: '导入', SEED: '种子' }
const activeStep = computed(() => {
  const index = lifecycleSteps.indexOf(detail.value?.status)
  return index === -1 ? lifecycleSteps.length : index + 1
})
const bindingDefinitions = computed(() => {
  const states = Object.fromEntries((detail.value?.bindings || []).map((item) => [item.binding_id, item]))
  return (detail.value?.content.bindings || []).map((binding) => ({
    ...binding,
    state: states[binding.binding_id] || null,
    mapped: Object.entries(binding.field_map).map(([name, mapping]) => `${name} ← ${mapping.source_field}`),
  }))
})
const pendingBindings = computed(() => (detail.value?.bindings || []).filter((item) => item.status !== 'VERIFIED'))

function formatTime(value) {
  return value
    ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
    : '—'
}
function errorText(caught) {
  const listed = caught?.details?.errors
  if (Array.isArray(listed) && listed.length) return `${caught.message}：${listed.slice(0, 3).join('；')}`
  return caught?.message || '操作失败'
}

async function load() {
  loading.value = true
  error.value = null
  try {
    packages.value = await semanticListPackages()
    if (selectedPackage.value) await selectPackage(selectedPackage.value)
  } catch (caught) {
    error.value = caught
  } finally {
    loading.value = false
  }
}

async function selectPackage(packageId) {
  selectedPackage.value = packageId
  versionsLoading.value = true
  try {
    versions.value = await semanticListVersions(packageId)
  } catch (caught) {
    ElMessage.error(errorText(caught))
  } finally {
    versionsLoading.value = false
  }
}

async function openVersion(versionId) {
  try {
    detail.value = await semanticGetVersion(versionId)
    validation.value = null
    detailTab.value = 'objects'
    detailVisible.value = true
  } catch (caught) {
    ElMessage.error(errorText(caught))
  }
}

async function applyAction(action, successMessage) {
  busy.value = true
  try {
    detail.value = await action()
    ElMessage.success(successMessage)
    await load()
    return true
  } catch (caught) {
    if (caught?.code === 'semantic_validation_failed' && caught.details?.issues) {
      validation.value = { valid: false, issues: caught.details.issues, changes: [], compared_to_version: null }
      detailTab.value = 'validation'
    }
    ElMessage.error(errorText(caught))
    return false
  } finally {
    busy.value = false
  }
}

async function runValidation() {
  busy.value = true
  try {
    validation.value = await semanticValidate(detail.value.version_id)
    detailTab.value = 'validation'
  } catch (caught) {
    ElMessage.error(errorText(caught))
  } finally {
    busy.value = false
  }
}

const review = () => applyAction(() => semanticReview(detail.value.version_id), '已完成技术评审')
const verifyBinding = (bindingId) => applyAction(
  () => semanticVerifyBinding(detail.value.version_id, bindingId),
  '已确认来源绑定',
)
const verify = () => applyAction(() => semanticVerify(detail.value.version_id), '数据负责人已确认')

async function publish() {
  busy.value = true
  try {
    detail.value = await semanticPublish(detail.value.version_id, false)
    ElMessage.success('已发布')
    await load()
  } catch (caught) {
    const breaking = caught?.details?.breaking_changes
    if (caught?.code !== 'semantic_breaking_changes_unacknowledged' || !Array.isArray(breaking)) {
      ElMessage.error(errorText(caught))
      return
    }
    const lines = breaking.map((item) => `${item.subject}：${item.message}`).join('\n')
    try {
      await ElMessageBox.confirm(
        `相对当前已发布版本存在 ${breaking.length} 项破坏性变化，已依赖旧版本的查询可能失效：\n\n${lines}`,
        '确认发布破坏性变化',
        { type: 'warning', confirmButtonText: '已评估影响，继续发布', cancelButtonText: '取消', customStyle: { whiteSpace: 'pre-wrap' } },
      )
    } catch {
      return
    }
    await applyAction(() => semanticPublish(detail.value.version_id, true), '已发布')
  } finally {
    busy.value = false
  }
}

async function retireOrDeprecate(kind) {
  const label = kind === 'deprecate' ? '弃用' : '退役'
  try {
    await ElMessageBox.confirm(
      kind === 'deprecate'
        ? '弃用后该版本仍可被读取，但不再是当前发布版本。'
        : '退役后该版本不可再被模型构建和 MCP 读取。',
      `确认${label}`,
      { type: 'warning', confirmButtonText: label, cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  const call = kind === 'deprecate' ? semanticDeprecate : semanticRetire
  await applyAction(() => call(detail.value.version_id), `已${label}`)
}

async function exportVersion(format) {
  try {
    const exported = await semanticExportVersion(detail.value.version_id, format)
    const url = URL.createObjectURL(new Blob([exported.document], { type: 'text/plain;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = exported.filename
    anchor.click()
    URL.revokeObjectURL(url)
  } catch (caught) {
    ElMessage.error(errorText(caught))
  }
}

// --- 从契约生成 ---
const generateVisible = ref(false)
const activeContracts = ref([])
const generateForm = reactive({ package_id: '', display_name: '', version: '0.1.0', owner: '', selected: [], keys: {} })
const contractKey = (row) => `${row.source_application_id}/${row.object_type}/${row.contract_version}`

async function openGenerate() {
  Object.assign(generateForm, { package_id: selectedPackage.value || '', display_name: '', version: '0.1.0', owner: '', selected: [], keys: {} })
  generateVisible.value = true
  if (!canReadContracts.value) return
  try {
    activeContracts.value = (await ingestListContracts()).filter((row) => row.status === 'ACTIVE')
  } catch (caught) {
    ElMessage.error(errorText(caught))
  }
}

async function submitGenerate() {
  if (!generateForm.package_id || !generateForm.display_name || !generateForm.selected.length) {
    ElMessage.warning('语义包标识、名称和至少一个契约为必填')
    return
  }
  const byKey = Object.fromEntries(activeContracts.value.map((row) => [contractKey(row), row]))
  busy.value = true
  try {
    const created = await semanticGenerate({
      package_id: generateForm.package_id,
      display_name: generateForm.display_name,
      version: generateForm.version,
      owner: generateForm.owner,
      contracts: generateForm.selected.map((key) => ({
        source_application_id: byKey[key].source_application_id,
        object_type: byKey[key].object_type,
        contract_version: byKey[key].contract_version,
        key_fields: (generateForm.keys[key] || '').split(',').map((item) => item.trim()).filter(Boolean),
      })),
    })
    generateVisible.value = false
    ElMessage.success(`已生成草稿，${created.warning_count} 项待审核提示`)
    selectedPackage.value = created.package_id
    await load()
    await openVersion(created.version_id)
    detailTab.value = created.warning_count ? 'warnings' : 'objects'
  } catch (caught) {
    ElMessage.error(errorText(caught))
  } finally {
    busy.value = false
  }
}

// --- 导入版本 ---
const importVisible = ref(false)
const importForm = reactive({ package_id: '', format: 'yaml', document: '' })

function openImport() {
  Object.assign(importForm, { package_id: selectedPackage.value || '', format: 'yaml', document: '' })
  importVisible.value = true
}

async function submitImport() {
  if (!importForm.package_id || !importForm.document.trim()) {
    ElMessage.warning('语义包标识和文档内容为必填')
    return
  }
  busy.value = true
  try {
    const created = await semanticImport(importForm.package_id, {
      format: importForm.format,
      document: importForm.document,
    })
    importVisible.value = false
    ElMessage.success('已导入为草稿版本')
    selectedPackage.value = created.package_id
    await load()
    await openVersion(created.version_id)
  } catch (caught) {
    ElMessage.error(errorText(caught))
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-shell">
    <PageHeader title="语义中心" description="语义包的生成、审核与发布。">
      <span class="header-hint">只有已发布的语义包会被模型构建和 MCP 读取；自动生成的内容始终从草稿开始。</span>
      <template #actions>
        <el-button v-if="canWrite" @click="openImport"><el-icon><Upload /></el-icon>导入版本</el-button>
        <el-button v-if="canWrite" type="primary" @click="openGenerate"><el-icon><MagicStick /></el-icon>从契约生成</el-button>
        <el-button @click="load"><el-icon><Refresh /></el-icon>刷新</el-button>
      </template>
    </PageHeader>

    <ApiState :loading="loading" :error="error" :empty="!packages.length" empty-text="还没有语义包，可从已生效的数据契约生成草稿" @retry="load">
      <section class="surface-panel page-section">
        <el-table :data="packages" highlight-current-row style="width: 100%" @row-click="(row) => selectPackage(row.package_id)">
          <el-table-column label="语义包" min-width="220">
            <template #default="scope">
              <strong>{{ scope.row.display_name }}</strong>
              <small class="subline mono">{{ scope.row.package_id }}</small>
            </template>
          </el-table-column>
          <el-table-column label="当前发布版本" width="150">
            <template #default="scope"><span class="mono">{{ scope.row.published_version || '未发布' }}</span></template>
          </el-table-column>
          <el-table-column label="最新版本" width="150">
            <template #default="scope"><span class="mono">{{ scope.row.latest_version }}</span></template>
          </el-table-column>
          <el-table-column label="最新版本状态" width="130">
            <template #default="scope"><StatusTag :status="scope.row.latest_status" /></template>
          </el-table-column>
          <el-table-column prop="version_count" label="版本数" width="90" />
          <el-table-column label="更新时间" width="180">
            <template #default="scope">{{ formatTime(scope.row.updated_at) }}</template>
          </el-table-column>
        </el-table>
      </section>

      <section v-if="selectedPackage" v-loading="versionsLoading" class="surface-panel page-section">
        <h3 class="panel-title">版本 · <span class="mono">{{ selectedPackage }}</span></h3>
        <el-table :data="versions" style="width: 100%">
          <el-table-column label="版本" width="130">
            <template #default="scope"><span class="mono">{{ scope.row.version }}</span></template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="scope"><StatusTag :status="scope.row.status" /></template>
          </el-table-column>
          <el-table-column label="来源" width="110">
            <template #default="scope">{{ originLabels[scope.row.origin] || scope.row.origin }}</template>
          </el-table-column>
          <el-table-column label="内容" min-width="240">
            <template #default="scope">
              {{ scope.row.object_count }} 对象 · {{ scope.row.relation_count }} 关系 · {{ scope.row.metric_count }} 指标 · {{ scope.row.binding_count }} 绑定
            </template>
          </el-table-column>
          <el-table-column label="创建" min-width="200">
            <template #default="scope">{{ scope.row.created_by }} · {{ formatTime(scope.row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="scope"><el-button link type="primary" @click="openVersion(scope.row.version_id)">查看</el-button></template>
          </el-table-column>
        </el-table>
      </section>
    </ApiState>

    <el-drawer v-model="detailVisible" size="min(1080px, 96vw)" :title="detail ? `${detail.display_name} · ${detail.version}` : ''">
      <template v-if="detail">
        <div class="detail-head">
          <StatusTag :status="detail.status" size="default" />
          <span class="mono digest">{{ detail.digest }}</span>
          <span class="spacer" />
          <el-dropdown @command="exportVersion">
            <el-button>导出<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="yaml">YAML</el-dropdown-item>
                <el-dropdown-item command="json">JSON</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button :loading="busy" @click="runValidation">校验</el-button>
          <el-button v-if="detail.status === 'DRAFT' && canReview" type="primary" :loading="busy" @click="review">技术评审通过</el-button>
          <el-button v-if="detail.status === 'REVIEWED' && canVerify" type="primary" :loading="busy" :disabled="pendingBindings.length > 0" @click="verify">数据负责人确认</el-button>
          <el-button v-if="detail.status === 'VERIFIED' && canPublish" type="primary" :loading="busy" @click="publish">发布</el-button>
          <el-button v-if="detail.status === 'PUBLISHED' && canPublish" :loading="busy" @click="retireOrDeprecate('deprecate')">弃用</el-button>
          <el-button v-if="detail.status === 'DEPRECATED' && canPublish" type="danger" plain :loading="busy" @click="retireOrDeprecate('retire')">退役</el-button>
        </div>

        <el-steps v-if="lifecycleSteps.includes(detail.status)" :active="activeStep" finish-status="success" align-center class="lifecycle">
          <el-step v-for="step in lifecycleSteps" :key="step" :title="lifecycleLabels[step]" />
        </el-steps>
        <p v-if="detail.status === 'REVIEWED' && pendingBindings.length" class="form-hint">
          还有 {{ pendingBindings.length }} 个来源绑定待数据负责人确认，全部确认后才能确认语义包。
        </p>
        <dl class="facts">
          <div><dt>评审</dt><dd>{{ detail.reviewed_by || '—' }} · {{ formatTime(detail.reviewed_at) }}</dd></div>
          <div><dt>确认</dt><dd>{{ detail.verified_by || '—' }} · {{ formatTime(detail.verified_at) }}</dd></div>
          <div><dt>发布</dt><dd>{{ detail.published_by || '—' }} · {{ formatTime(detail.published_at) }}</dd></div>
        </dl>

        <el-tabs v-model="detailTab">
          <el-tab-pane :label="`对象 (${detail.object_count})`" name="objects">
            <el-collapse>
              <el-collapse-item v-for="object in detail.content.objects" :key="object.semantic_id" :name="object.semantic_id">
                <template #title>
                  <strong>{{ object.display_name }}</strong>
                  <span class="mono subtle">{{ object.semantic_id }}</span>
                  <span class="subtle">主键 {{ object.keys.join(', ') }}</span>
                </template>
                <p v-if="object.description" class="form-hint">{{ object.description }}</p>
                <el-table :data="object.properties" size="small">
                  <el-table-column prop="name" label="属性" min-width="150">
                    <template #default="scope"><span class="mono">{{ scope.row.name }}</span></template>
                  </el-table-column>
                  <el-table-column prop="display_name" label="名称" min-width="130" />
                  <el-table-column label="类型" width="170">
                    <template #default="scope">
                      {{ scope.row.type }}<span v-if="scope.row.ref" class="subtle"> → {{ scope.row.ref }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="必填" width="70">
                    <template #default="scope">{{ scope.row.required ? '是' : '' }}</template>
                  </el-table-column>
                  <el-table-column label="分类" width="150">
                    <template #default="scope">
                      {{ scope.row.classification }}
                      <el-tag v-if="scope.row.sensitive" type="warning" size="small" effect="plain">脱敏</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column prop="description" label="说明" min-width="200" />
                </el-table>
              </el-collapse-item>
            </el-collapse>
          </el-tab-pane>

          <el-tab-pane :label="`关系 (${detail.relation_count})`" name="relations">
            <el-table :data="detail.content.relations" size="small">
              <el-table-column prop="semantic_id" label="关系" min-width="240" />
              <el-table-column label="方向" min-width="320">
                <template #default="scope">{{ scope.row.source_object }} ({{ scope.row.source_cardinality }}) → {{ scope.row.target_object }} ({{ scope.row.target_cardinality }})</template>
              </el-table-column>
              <el-table-column prop="source_property" label="经由属性" width="150" />
            </el-table>
          </el-tab-pane>

          <el-tab-pane :label="`指标 (${detail.metric_count})`" name="metrics">
            <el-table :data="detail.content.metrics" size="small">
              <el-table-column prop="display_name" label="指标" min-width="150" />
              <el-table-column prop="formula" label="口径" min-width="280" />
              <el-table-column label="粒度" min-width="160">
                <template #default="scope">{{ scope.row.grain.join(' / ') }}</template>
              </el-table-column>
              <el-table-column label="状态" width="110">
                <template #default="scope"><StatusTag :status="scope.row.status" /></template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <el-tab-pane :label="`来源绑定 (${detail.binding_count})`" name="bindings">
            <el-table :data="bindingDefinitions" size="small">
              <el-table-column label="语义对象" min-width="200">
                <template #default="scope"><span class="mono">{{ scope.row.semantic_object }}</span></template>
              </el-table-column>
              <el-table-column label="来源契约" min-width="250">
                <template #default="scope">
                  {{ scope.row.source_application_id }} / {{ scope.row.object_type }}
                  <small class="subline mono">{{ scope.row.payload_contract_version }}</small>
                </template>
              </el-table-column>
              <el-table-column label="字段映射" min-width="260">
                <template #default="scope">
                  <small v-for="line in scope.row.mapped" :key="line" class="subline mono">{{ line }}</small>
                </template>
              </el-table-column>
              <el-table-column label="确认" width="210">
                <template #default="scope">
                  <template v-if="scope.row.state">
                    <StatusTag :status="scope.row.state.status" />
                    <small v-if="scope.row.state.verified_by" class="subline">{{ scope.row.state.verified_by }}</small>
                    <el-button
                      v-if="detail.status === 'REVIEWED' && scope.row.state.status !== 'VERIFIED' && canVerify"
                      link type="primary" :loading="busy" @click="verifyBinding(scope.row.binding_id)"
                    >确认绑定</el-button>
                  </template>
                  <span v-else class="subtle">已停用</span>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!bindingDefinitions.length" description="该版本只定义含义，尚未绑定数据来源" :image-size="64" />
          </el-tab-pane>

          <el-tab-pane :label="`生成提示 (${detail.warning_count})`" name="warnings">
            <el-table :data="detail.warnings" size="small">
              <el-table-column prop="subject" label="对象" min-width="260">
                <template #default="scope"><span class="mono">{{ scope.row.subject }}</span></template>
              </el-table-column>
              <el-table-column prop="message" label="需审核人确认" min-width="420" />
            </el-table>
          </el-tab-pane>

          <el-tab-pane label="校验结果" name="validation">
            <el-empty v-if="!validation" description="点击上方“校验”，按已登记契约和当前发布版本检查" :image-size="64" />
            <template v-else>
              <el-alert
                :type="validation.valid ? 'success' : 'error'" :closable="false" show-icon
                :title="validation.valid ? '通过：绑定与已登记契约一致' : '未通过：存在必须修复的问题'"
              />
              <el-table v-if="validation.issues.length" :data="validation.issues" size="small" class="page-section">
                <el-table-column label="级别" width="90">
                  <template #default="scope"><el-tag :type="scope.row.severity === 'error' ? 'danger' : 'warning'" size="small">{{ scope.row.severity === 'error' ? '错误' : '提示' }}</el-tag></template>
                </el-table-column>
                <el-table-column prop="subject" label="对象" min-width="240" />
                <el-table-column prop="message" label="说明" min-width="420" />
              </el-table>
              <template v-if="validation.compared_to_version">
                <h4 class="panel-title">相对已发布版本 {{ validation.compared_to_version }} 的变化</h4>
                <el-table :data="validation.changes" size="small">
                  <el-table-column label="影响" width="110">
                    <template #default="scope"><el-tag :type="scope.row.breaking ? 'danger' : 'info'" size="small">{{ scope.row.breaking ? '破坏性' : '兼容' }}</el-tag></template>
                  </el-table-column>
                  <el-table-column prop="subject" label="对象" min-width="260" />
                  <el-table-column prop="message" label="变化" min-width="380" />
                </el-table>
              </template>
            </template>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>

    <el-dialog v-model="generateVisible" title="从数据契约生成语义草稿" width="min(760px, 94vw)">
      <el-form label-width="110px">
        <el-form-item label="语义包标识" required>
          <el-input v-model="generateForm.package_id" placeholder="小写字母、数字、下划线，如 manufacturing" />
        </el-form-item>
        <el-form-item label="名称" required><el-input v-model="generateForm.display_name" /></el-form-item>
        <el-form-item label="版本" required><el-input v-model="generateForm.version" /></el-form-item>
        <el-form-item label="数据负责人"><el-input v-model="generateForm.owner" /></el-form-item>
        <el-form-item label="数据契约" required>
          <el-select v-model="generateForm.selected" multiple filterable placeholder="选择已生效的契约" style="width: 100%">
            <el-option v-for="row in activeContracts" :key="contractKey(row)" :label="contractKey(row)" :value="contractKey(row)" />
          </el-select>
          <span v-if="!canReadContracts" class="form-hint">需要数据接入的读取权限才能列出契约。</span>
        </el-form-item>
        <el-form-item v-for="key in generateForm.selected" :key="key" :label="key.split('/')[1]">
          <el-input v-model="generateForm.keys[key]" placeholder="业务键字段，逗号分隔；留空则使用来源对象编号" />
        </el-form-item>
        <p class="form-hint">生成结果为草稿：属性名称、业务键、敏感等级和对象关系都需要审核人确认，不会自动发布。</p>
      </el-form>
      <template #footer>
        <el-button @click="generateVisible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="submitGenerate">生成草稿</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="importVisible" title="导入语义包版本" width="min(820px, 94vw)">
      <el-form label-width="110px">
        <el-form-item label="语义包标识" required><el-input v-model="importForm.package_id" /></el-form-item>
        <el-form-item label="格式">
          <el-radio-group v-model="importForm.format">
            <el-radio-button value="yaml">YAML</el-radio-button>
            <el-radio-button value="json">JSON</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="文档" required>
          <el-input v-model="importForm.document" type="textarea" :rows="16" class="mono" placeholder="粘贴从“导出”得到并修改后的语义包文档；version 必须是新的版本号" />
        </el-form-item>
        <p class="form-hint">已有版本的内容不可修改。修改语义包时导出现有版本、改好后以新版本号导入，再重新走评审。</p>
      </el-form>
      <template #footer>
        <el-button @click="importVisible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="submitImport">导入为草稿</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.header-hint { color: var(--ink-500); font-size: 13px; }
.subline { display: block; margin-top: 3px; color: var(--ink-500); font-size: 11px; }
.subtle { margin-left: 10px; color: var(--ink-500); font-size: 12px; font-weight: 400; }
.panel-title { margin: 16px 16px 10px; font-size: 14px; }
.detail-head { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.detail-head .spacer { flex: 1; }
.digest { color: var(--ink-500); font-size: 11px; word-break: break-all; }
.lifecycle { margin: 20px 0 8px; }
.facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 16px; margin: 12px 0 4px; }
.facts dt { color: var(--ink-500); font-size: 12px; }
.facts dd { margin: 2px 0 0; font-size: 13px; }
.form-hint { color: var(--ink-500); font-size: 12px; }
@media (max-width: 700px) { .facts { grid-template-columns: 1fr; } }
</style>
