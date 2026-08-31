// 数据接入（M7）：门户权威配置与同步维护操作，对接 platform ingest API。
import { apiRequest } from './platformApi'

export async function ingestGetConfig() {
  return apiRequest('ingest/config')
}

export async function ingestSavePolicy(policy) {
  return apiRequest('ingest/policy', { method: 'PUT', body: policy })
}

export async function ingestSaveSource(source) {
  return apiRequest('ingest/sources', { method: 'PUT', body: source })
}

export async function ingestRunSync(mode) {
  return apiRequest('ingest/actions/sync', { method: 'POST', body: { mode } })
}

export async function ingestRunReconcile() {
  return apiRequest('ingest/actions/reconcile', { method: 'POST', body: {} })
}

export async function ingestRunRebuild({ mode, sourceApplicationId, objectType }) {
  return apiRequest('ingest/actions/rebuild', {
    method: 'POST',
    body: {
      mode,
      source_application_id: sourceApplicationId,
      object_type: objectType,
      confirm: true,
    },
  })
}

export async function ingestRunPrune(dryRun) {
  return apiRequest('ingest/actions/prune', { method: 'POST', body: { dry_run: dryRun } })
}

export async function ingestListContracts() {
  return apiRequest('ingest/contracts')
}

export async function ingestSaveContract(body) {
  return apiRequest('ingest/contracts', { method: 'PUT', body })
}

export async function ingestInferContract(body) {
  return apiRequest('ingest/contracts/infer', { method: 'POST', body })
}

export async function ingestActivateContract(body) {
  return apiRequest('ingest/contracts/activate', { method: 'POST', body })
}

export async function ingestRejectContract(body) {
  return apiRequest('ingest/contracts/reject', { method: 'POST', body })
}

export async function ingestListCertifications() {
  return apiRequest('ingest/contracts/certifications')
}

export async function ingestCreateCertification(body) {
  return apiRequest('ingest/contracts/certifications', { method: 'POST', body })
}

export async function ingestApproveCertification(certificationId) {
  return apiRequest(`ingest/contracts/certifications/${certificationId}/approve`, {
    method: 'POST',
    body: {},
  })
}
