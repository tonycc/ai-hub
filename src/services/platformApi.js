const API_PREFIX = '/portal-api/v1'

let loginRedirectPending = false

function redirectToLogin() {
  if (loginRedirectPending) return
  loginRedirectPending = true
  const returnTo = `${window.location.pathname}${window.location.search}${window.location.hash}`
  window.location.assign(`/auth/login?return_to=${encodeURIComponent(returnTo)}`)
}

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

export function formatApiError(error) {
  if (!(error instanceof PlatformApiError)) return error?.message || '操作失败'
  const validationErrors = error.details?.errors
  if (!Array.isArray(validationErrors) || !validationErrors.length) return error.message

  const fieldLabels = {
    login_account: '登录账号',
    user_name: '用户姓名',
    password: '密码',
    organization_id: '所属组织',
    position_code: '职位',
    email: '邮箱',
    role_code: '平台角色',
    application_id: '应用编号',
  }

  return validationErrors.map((item) => {
    const field = fieldLabels[item.loc?.at(-1)] || item.loc?.at(-1) || '字段'
    const message = item.msg?.startsWith('Value error, ')
      ? item.msg.slice('Value error, '.length)
      : item.msg
    return `${field}：${message}`
  }).join('；')
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
  if (response.status === 401) redirectToLogin()
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

export async function fetchAssetText(path) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    cache: 'no-store',
  })
  if (!response.ok) throw await parseError(response)
  return response.text()
}

export function queryString(values) {
  const params = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => {
    if (value !== '' && value !== null && value !== undefined) params.set(key, String(value))
  })
  const query = params.toString()
  return query ? `?${query}` : ''
}
