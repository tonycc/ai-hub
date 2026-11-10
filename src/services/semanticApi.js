// 语义中心（ADR-035 / C2）：语义包版本的生成、导入、校验、评审、确认与发布。
import { apiRequest } from './platformApi'

export async function semanticListPackages() {
  return apiRequest('semantics/packages')
}

export async function semanticListVersions(packageId) {
  return apiRequest(`semantics/packages/${encodeURIComponent(packageId)}/versions`)
}

export async function semanticGetVersion(versionId) {
  return apiRequest(`semantics/versions/${versionId}`)
}

export async function semanticExportVersion(versionId, format = 'yaml') {
  return apiRequest(`semantics/versions/${versionId}/export?format=${format}`)
}

export async function semanticGenerate(body) {
  return apiRequest('semantics/generations', { method: 'POST', body })
}

export async function semanticImport(packageId, body) {
  return apiRequest(`semantics/packages/${encodeURIComponent(packageId)}/versions`, {
    method: 'POST',
    body,
  })
}

export async function semanticValidate(versionId) {
  return apiRequest(`semantics/versions/${versionId}/validate`, { method: 'POST', body: {} })
}

export async function semanticReview(versionId) {
  return apiRequest(`semantics/versions/${versionId}/review`, { method: 'POST', body: {} })
}

export async function semanticVerifyBinding(versionId, bindingId) {
  return apiRequest(`semantics/versions/${versionId}/bindings/verify`, {
    method: 'POST',
    body: { binding_id: bindingId },
  })
}

export async function semanticVerify(versionId) {
  return apiRequest(`semantics/versions/${versionId}/verify`, { method: 'POST', body: {} })
}

export async function semanticPublish(versionId, acknowledgeBreaking = false) {
  return apiRequest(`semantics/versions/${versionId}/publish`, {
    method: 'POST',
    body: { acknowledge_breaking: acknowledgeBreaking },
  })
}

export async function semanticDeprecate(versionId) {
  return apiRequest(`semantics/versions/${versionId}/deprecate`, { method: 'POST', body: {} })
}

export async function semanticRetire(versionId) {
  return apiRequest(`semantics/versions/${versionId}/retire`, { method: 'POST', body: {} })
}
