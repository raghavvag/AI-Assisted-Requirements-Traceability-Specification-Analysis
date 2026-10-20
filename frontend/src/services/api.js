// frontend/src/services/api.js
// Axios instance with automatic JWT Bearer injection and 401 → redirect
import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 60000,   // 60s — analysis pipeline can be slow on first run
})

// Inject JWT token from localStorage on every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// On 401 for an authenticated resource, clear tokens and redirect to /login.
// IMPORTANT: never force-redirect on the auth endpoints themselves — a failed
// /auth/google or /auth/me must surface its real error to the caller (otherwise
// a misconfigured Google Client ID looks like an infinite "back to login" loop).
const AUTH_PATHS = ['/auth/google', '/auth/refresh', '/auth/me']

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    const url = error.config?.url || ''
    const isAuthCall = AUTH_PATHS.some((p) => url.includes(p))
    if (error.response?.status === 401 && !isAuthCall) {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('user')
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  }
)

export default api

// ── API helpers ──────────────────────────────────────────────────────────────

export const authApi = {
  googleLogin: (idToken) => api.post('/auth/google', { id_token: idToken }),
  me:          ()         => api.get('/auth/me'),
  refresh:     (token)   => api.post('/auth/refresh', null, { params: { refresh_token: token } }),
}

export const projectsApi = {
  list:     ()              => api.get('/projects'),
  get:      (id)            => api.get(`/projects/${id}`),
  create:   (data)          => api.post('/projects', data),
  delete:   (id)            => api.delete(`/projects/${id}`),
  upload:   (id, formData)  => api.post(`/projects/${id}/upload`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }),
  docs:     (id)            => api.get(`/projects/${id}/documents`),
  srsRequirements: (id)     => api.get(`/projects/${id}/srs/requirements`),
}

/**
 * Human-readable message from an API error. FastAPI returns `detail` as a
 * string, a {message, report} object (SRS extraction 422s) or a validation
 * error list.
 */
export function apiError(err, fallback = 'Request failed.') {
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg
  return fallback
}

export const analysisApi = {
  analyze:   (id, opts)    => api.post(`/projects/${id}/analyze`, opts),
  matrix:    (id)          => api.get(`/projects/${id}/matrix`),
  drift:     (id)          => api.get(`/projects/${id}/drift`),
  prReview:  (id, scenario) => api.post(`/projects/${id}/pr-review`, null, { params: { scenario } }),
  benchmark: (id)          => api.get(`/projects/${id}/benchmark`),
  explain:   (id, reqId)   => api.get(`/projects/${id}/explain/${reqId}`),
}

// GitHub App (live PR reviews). params: { repo: 'owner/name', pr_number, limit }
export const githubApi = {
  reviews: (params)        => api.get('/github/reviews', { params }),
  review:  (reviewId)      => api.get(`/github/reviews/${reviewId}`),
}
