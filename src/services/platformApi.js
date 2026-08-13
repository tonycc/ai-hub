const API_PREFIX = '/portal-api/v1'

export class PlatformApiError extends Error {
  constructor(status, code, message, requestId = null, details = {}) {
    super(message)
    this.name = 'PlatformApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
    this.details = details
  }
}

function cookieValue(name) {
  const prefix = `${encodeURIComponent(name)}=`
  const value = document.cookie.split('; ').find((item) => item.startsWith(prefix))
  return value ? decodeURIComponent(value.slice(prefix.length)) : null
}

async function parseError(response) {
  let payload = null
  try {
    payload = await response.json()
  } catch {
    payload = null
  }
  const message = payload?.message || `请求失败（HTTP ${response.status}）`
  return new PlatformApiError(
    response.status,
    payload?.error_code || 'http_error',
    message,
    payload?.request_id || response.headers.get('x-request-id'),
    payload?.details || {},
  )
}

export async function apiRequest(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase()
  const headers = new Headers(options.headers || {})
  if (options.body !== undefined && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrfToken = cookieValue('ai_hub_portal_csrf')
    if (csrfToken) headers.set('X-CSRF-Token', csrfToken)
  }
  const target = path.startsWith('/auth/')
    ? path
    : path.startsWith('/portal-api/')
      ? path
      : `${API_PREFIX}/${path.replace(/^\//, '')}`
  const response = await fetch(target, {
    credentials: 'same-origin',
    cache: 'no-store',
    ...options,
    method,
    headers,
    body: options.body === undefined || options.body instanceof FormData
      ? options.body
      : JSON.stringify(options.body),
  })
  if (!response.ok) throw await parseError(response)
  if (response.status === 204) return null
  return response.json()
}

export async function downloadAsset(path, filename) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    cache: 'no-store',
  })
  if (!response.ok) throw await parseError(response)
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

export function queryString(values) {
  const params = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => {
    if (value !== '' && value !== null && value !== undefined) params.set(key, String(value))
  })
  const query = params.toString()
  return query ? `?${query}` : ''
}
