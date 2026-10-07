// API client.
//
// Everything network-facing lives here so error handling is consistent.
// Previously each caller did its own fetch and every failure surfaced as
// "Something went wrong", because a non-2xx response often returned HTML
// (Django's default error pages) and `await response.json()` threw a parse
// error that masked the real message.
//
// The backend now returns `{"error": "...", "code": "..."}` for every
// failure, and `request()` below guarantees we surface that text — or, if
// the response wasn't JSON at all (proxy error, timeout), something honest
// about that instead.

// Vite exposes env vars prefixed with VITE_ at build time only.
//
// The default is a RELATIVE path, which in development hits the dev-server
// proxy in vite.config.js and lands on the Django server same-origin — so
// local development needs no CORS configuration at all. In production,
// VITE_API_BASE_URL points at the real backend (a different origin), where
// the backend's CORS_ALLOWED_ORIGINS must list the frontend's origin.
export const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

export class ApiError extends Error {
  constructor(message, { status = 0, code = null, detail = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
  }

  get isUnauthorized() {
    return this.status === 401
  }

  get isConflict() {
    return this.status === 409
  }

  get isRateLimited() {
    return this.status === 429
  }
}

/** Called when the server rejects our token, so the app can log out. */
let onUnauthorized = null
export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler
}

export function getAuthHeaders() {
  const token = localStorage.getItem('authToken')
  return token ? { Authorization: `Token ${token}` } : {}
}

export function logoutUser() {
  localStorage.removeItem('authToken')
  localStorage.removeItem('username')
}

/**
 * Parses a response body defensively.
 *
 * A 502 from a reverse proxy or a connection reset has no JSON body at all;
 * reporting that as a parse failure ("Unexpected token < in JSON") is
 * exactly the kind of misleading error this module exists to prevent.
 */
async function readBody(response) {
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return { __unparseable: text.slice(0, 200) }
  }
}

function messageFor(status, body, fallback) {
  if (body && typeof body.error === 'string' && body.error.trim()) {
    return body.error
  }
  if (status === 0) return 'Could not reach the server. Check your connection and try again.'
  if (status === 401) return 'Your session has expired. Please log in again.'
  if (status === 403) return 'You do not have permission to do that.'
  if (status === 404) return 'That could not be found.'
  if (status === 429) return 'Too many attempts. Please wait a moment and try again.'
  if (status >= 500) return 'Something went wrong on our end. Please try again.'
  return fallback
}

/**
 * Default request timeout.
 *
 * fetch() has no timeout of its own, so a request to a backend that accepts the
 * connection and then never answers stays pending forever. The polling loop
 * survives that (its gap guard prevents overlap), but the progress panel just
 * quietly stops updating with no error anywhere, which reads as "the send
 * finished" when it has not.
 */
const DEFAULT_TIMEOUT_MS = 30000

async function request(
  path,
  { method = 'GET', body, headers = {}, auth = true, timeout = DEFAULT_TIMEOUT_MS } = {}
) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)

  let response
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method,
      signal: controller.signal,
      headers: {
        ...(body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...(auth ? getAuthHeaders() : {}),
        ...headers,
      },
      body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
    })
  } catch (err) {
    // Distinguish "we gave up waiting" from "never reached the server"; they
    // need different advice and the browser reports both as a bare TypeError.
    if (err?.name === 'AbortError') {
      throw new ApiError(
        `The server did not respond within ${Math.round(timeout / 1000)}s. It may be busy or unreachable.`,
        { status: 0, code: 'timeout' }
      )
    }
    // Network-level failure: offline, DNS, connection refused, CORS preflight.
    throw new ApiError(messageFor(0, null, 'Request failed'), { status: 0 })
  } finally {
    clearTimeout(timer)
  }

  const parsed = await readBody(response)

  if (!response.ok) {
    const error = new ApiError(messageFor(response.status, parsed, 'Request failed'), {
      status: response.status,
      code: parsed?.code ?? null,
      detail: parsed,
    })

    // A dead token means every subsequent request will fail too. Clear it
    // once here rather than leaving the app in a broken half-logged-in state.
    if (error.isUnauthorized && auth) {
      logoutUser()
      if (onUnauthorized) onUnauthorized()
    }
    throw error
  }

  return parsed
}

export function registerUser({ username, password, email }) {
  return request('/auth/register/', {
    method: 'POST',
    body: { username, password, email },
    auth: false,
  })
}

export function loginUser({ username, password }) {
  return request('/auth/login/', {
    method: 'POST',
    body: { username, password },
    auth: false,
  })
}

export function getWhatsappNumber() {
  return request('/profile/whatsapp-number/')
}

export function updateWhatsappNumber(whatsappNumber) {
  return request('/profile/whatsapp-number/', {
    method: 'PUT',
    body: { whatsapp_number: whatsappNumber },
  })
}

export function getCampaignStatus(campaignId) {
  return request(`/campaigns/${campaignId}/status/`)
}

/**
 * The caller's campaigns, newest first.
 *
 * Returns summary rows only — no contacts, no message body — so the list stays
 * cheap even with large campaigns behind it.
 */
export function listCampaigns({ page = 1, pageSize = 25 } = {}) {
  return request(`/campaigns/?page=${page}&page_size=${pageSize}`)
}

/**
 * Returns failed contacts to pending so the campaign can be sent again.
 * Does not restart the send; call startSending() afterwards.
 */
export function retryCampaign(campaignId) {
  return request(`/campaigns/${campaignId}/retry/`, { method: 'POST' })
}

export function deleteCampaign(campaignId) {
  return request(`/campaigns/${campaignId}/`, { method: 'DELETE' })
}

/**
 * Creates a campaign.
 *
 * The backend rejects the whole request if any contact has an unusable phone
 * number, rather than quietly dropping it, and returns `rejected` examples.
 * Those are attached to the thrown error so the UI can name the offending
 * numbers instead of saying "invalid contacts".
 */
export async function createCampaign({ name, messageText, sendMode, contacts, mediaFiles }) {
  const formData = new FormData()
  formData.append('name', name)
  formData.append('message_text', messageText)
  formData.append('send_mode', sendMode)
  formData.append('contacts', JSON.stringify(contacts))
  ;(mediaFiles || []).forEach((file) => formData.append('media_files', file))

  try {
    // Longer than the default: this is the one call that ships file bytes, so
    // the 30s budget would abort a legitimate upload on a slow connection.
    return await request('/campaigns/create/', {
      method: 'POST',
      body: formData,
      timeout: 120000,
    })
  } catch (err) {
    if (err.code === 'invalid_contacts' && err.detail?.rejected) {
      err.invalidContacts = err.detail.rejected
      err.rejectedCount = err.detail.rejected_count
    }
    throw err
  }
}

export function startSending(campaignId) {
  return request(`/campaigns/${campaignId}/start/`, { method: 'POST' })
}

/**
 * Current state of this user's WhatsApp handshake.
 *
 * Was duplicated inline in LoginCode.jsx with its own copy of the base URL
 * and token lookup; keeping it here means one place to change.
 */
export function getLoginStatus() {
  return request('/login-status/')
}

export function resetWhatsappSession() {
  return request('/whatsapp-session/reset/', { method: 'POST' })
}

/**
 * Fetches one campaign attachment as a Blob.
 *
 * Deliberately not a bare URL helper. The media endpoint is authenticated by
 * the same Token header as everything else, so a URL dropped into an <img src>
 * or an <a download> would 401 — the browser sends cookies, not an
 * Authorization header. Fetching the bytes here and handing back a blob is the
 * only thing that actually works.
 *
 * The caller owns the returned object URL and must revokeObjectURL it.
 */
export async function fetchCampaignMedia(campaignId, fileId) {
  const response = await fetch(
    `${BASE_URL}/campaigns/${campaignId}/media/${fileId}/`,
    { headers: getAuthHeaders() }
  )

  if (!response.ok) {
    const parsed = await readBody(response).catch(() => null)
    throw new ApiError(messageFor(response.status, parsed, 'Could not load the attachment'), {
      status: response.status,
      code: parsed?.code ?? null,
      detail: parsed,
    })
  }

  return response.blob()
}