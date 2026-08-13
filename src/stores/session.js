import { computed, reactive, readonly } from 'vue'
import { apiRequest, PlatformApiError } from '../services/platformApi'

const state = reactive({
  status: 'idle',
  principal: null,
  error: null,
})

let pendingLoad = null

async function loadSession({ force = false } = {}) {
  if (!force && state.status === 'ready') return state.principal
  if (pendingLoad) return pendingLoad
  state.status = 'loading'
  state.error = null
  pendingLoad = apiRequest('session')
    .then((principal) => {
      state.principal = principal
      state.status = 'ready'
      return principal
    })
    .catch((error) => {
      state.principal = null
      state.status = error instanceof PlatformApiError && error.status === 401 ? 'anonymous' : 'error'
      state.error = error
      throw error
    })
    .finally(() => {
      pendingLoad = null
    })
  return pendingLoad
}

function hasPermission(permission, applicationId = null) {
  if (!state.principal?.permissions?.includes(permission)) return false
  if (!applicationId) return true
  const scope = state.principal.application_scopes?.[permission]
  return scope === null || scope?.includes(applicationId) === true
}

async function logout() {
  await apiRequest('/auth/logout', { method: 'POST' })
  state.principal = null
  state.status = 'anonymous'
}

export function usePortalSession() {
  return {
    state: readonly(state),
    principal: computed(() => state.principal),
    authenticated: computed(() => state.status === 'ready'),
    loadSession,
    hasPermission,
    logout,
  }
}
