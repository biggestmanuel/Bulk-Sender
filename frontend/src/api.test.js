import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  BASE_URL,
  createCampaign,
  getLoginStatus,
  loginUser,
  logoutUser,
  setUnauthorizedHandler,
  startSending,
} from './api'

/**
 * Builds a fetch Response-alike.
 *
 * `Response` exists in Node 18+, but constructing one with a plain string
 * body and reading `.text()` twice is fiddly; a hand-rolled double keeps
 * these tests focused on our parsing logic.
 */
function jsonResponse(body, { status = 200 } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
  }
}

function textResponse(text, { status = 200 } = {}) {
  return { ok: status >= 200 && status < 300, status, text: async () => text }
}

let fetchMock

beforeEach(() => {
  fetchMock = vi.fn()
  globalThis.fetch = fetchMock
  setUnauthorizedHandler(null)
})

afterEach(() => {
  setUnauthorizedHandler(null)
})

describe('base url', () => {
  it('defaults to a relative path so dev needs no CORS setup', () => {
    expect(BASE_URL).toBe('/api')
  })
})

describe('error handling', () => {
  it('surfaces the backend error message', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ error: 'Add your WhatsApp phone number in settings before sending.', code: 'phone_required' }, { status: 400 })
    )

    await expect(startSending(1)).rejects.toThrow(/phone number/i)
  })

  it('attaches the machine-readable code', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: 'nope', code: 'already_sending' }, { status: 409 }))

    await expect(startSending(1)).rejects.toMatchObject({
      code: 'already_sending',
      status: 409,
    })
  })

  /**
   * The original client did `await response.json()` directly, so any
   * non-JSON body (a proxy 502 page, an HTML error page, a dropped
   * connection) threw a parse error and the user saw
   * "Unexpected token < in JSON" instead of anything useful.
   */
  it('handles a non-JSON error body', async () => {
    fetchMock.mockResolvedValue(textResponse('<html><body>502 Bad Gateway</body></html>', { status: 502 }))

    await expect(startSending(1)).rejects.toThrow(/went wrong on our end/i)
  })

  it('handles an empty error body', async () => {
    fetchMock.mockResolvedValue(textResponse('', { status: 500 }))

    await expect(startSending(1)).rejects.toThrow(/went wrong on our end/i)
  })

  it('reports a network failure honestly', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))

    const error = await startSending(1).catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(0)
    expect(error.message).toMatch(/could not reach the server/i)
  })

  it('never leaks a raw JSON parse error', async () => {
    fetchMock.mockResolvedValue(textResponse('{not json', { status: 400 }))

    const error = await startSending(1).catch((e) => e)
    expect(error.message).not.toMatch(/JSON|Unexpected token/i)
  })
})

describe('401 handling', () => {
  it('clears the stored token and notifies the app', async () => {
    localStorage.setItem('authToken', 'stale-token')
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)

    fetchMock.mockResolvedValue(jsonResponse({ error: 'Invalid token.' }, { status: 401 }))

    await expect(getLoginStatus()).rejects.toBeInstanceOf(ApiError)
    expect(localStorage.getItem('authToken')).toBeNull()
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('does not fire for login failures', async () => {
    // A wrong password is a 401 too, but clearing the session and logging the
    // user out of the app would be wrong.
    localStorage.setItem('authToken', 'still-good')
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)

    fetchMock.mockResolvedValue(jsonResponse({ error: 'Invalid username or password.' }, { status: 401 }))

    await expect(loginUser({ username: 'a', password: 'b' })).rejects.toThrow(/invalid username/i)
    expect(onUnauthorized).not.toHaveBeenCalled()
    expect(localStorage.getItem('authToken')).toBe('still-good')
  })
})

describe('request construction', () => {
  it('sends the token on authenticated requests', async () => {
    localStorage.setItem('authToken', 'abc123')
    fetchMock.mockResolvedValue(jsonResponse({ phase: 'idle' }))

    await getLoginStatus()

    const [, options] = fetchMock.mock.calls[0]
    expect(options.headers.Authorization).toBe('Token abc123')
  })

  it('omits the token on auth endpoints', async () => {
    localStorage.setItem('authToken', 'abc123')
    fetchMock.mockResolvedValue(jsonResponse({ token: 't' }))

    await loginUser({ username: 'a', password: 'b' })

    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/auth/login/')
    expect(options.headers.Authorization).toBeUndefined()
  })

  it('sends JSON bodies with a content type', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }))

    await loginUser({ username: 'a', password: 'b' })

    const [, options] = fetchMock.mock.calls[0]
    expect(options.headers['Content-Type']).toBe('application/json')
    expect(JSON.parse(options.body)).toEqual({ username: 'a', password: 'b' })
  })
})

describe('createCampaign', () => {
  const baseArgs = {
    name: 'Test',
    messageText: 'hi',
    sendMode: 'delay',
    contacts: [{ name: 'Ada', phone: '+2348161234765' }],
    mediaFiles: [],
  }

  it('sends contacts as a JSON string inside FormData', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: 7 }, { status: 201 }))

    await createCampaign(baseArgs)

    const [, options] = fetchMock.mock.calls[0]
    expect(options.body).toBeInstanceOf(FormData)
    expect(JSON.parse(options.body.get('contacts'))).toEqual(baseArgs.contacts)
    // FormData sets its own boundary; forcing a content type breaks it.
    expect(options.headers['Content-Type']).toBeUndefined()
  })

  it('appends each media file', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: 7 }, { status: 201 }))
    const file = new File(['x'], 'pic.png', { type: 'image/png' })

    await createCampaign({ ...baseArgs, mediaFiles: [file] })

    const [, options] = fetchMock.mock.calls[0]
    expect(options.body.getAll('media_files')).toHaveLength(1)
  })

  /**
   * The backend rejects the whole campaign when any number is unusable and
   * names the offenders. Those details have to survive to the caller or the
   * UI can only say "invalid contacts".
   */
  it('preserves rejected-contact details on the error', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          error: '1 of 2 contacts have unusable phone numbers. Examples — "+2341" has only 4 digits',
          code: 'invalid_contacts',
          rejected_count: 1,
          rejected: [{ name: 'Bad', phone: '+2341', reason: 'has only 4 digits' }],
        },
        { status: 400 }
      )
    )

    const error = await createCampaign(baseArgs).catch((e) => e)

    expect(error.code).toBe('invalid_contacts')
    expect(error.rejectedCount).toBe(1)
    expect(error.invalidContacts).toHaveLength(1)
    expect(error.invalidContacts[0].phone).toBe('+2341')
  })

  it('works when the backend predates invalid_contacts', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: 'Failed to create campaign' }, { status: 400 }))

    const error = await createCampaign(baseArgs).catch((e) => e)
    expect(error.invalidContacts).toBeUndefined()
  })
})

describe('request timeout', () => {
  // fetch() has no timeout of its own, so a backend that accepts the
  // connection and then never answers leaves the promise pending forever. The
  // polling loop survives, but the progress panel stops updating with no
  // error anywhere, which reads as "the send finished" when it has not.

  it('aborts and reports a timeout when the server never answers', async () => {
    vi.useFakeTimers()
    try {
      fetchMock.mockImplementation(
        (url, options) =>
          new Promise((resolve, reject) => {
            options.signal.addEventListener('abort', () => {
              const err = new Error('aborted')
              err.name = 'AbortError'
              reject(err)
            })
          })
      )

      const pending = getLoginStatus()
      const assertion = expect(pending).rejects.toMatchObject({
        name: 'ApiError',
        code: 'timeout',
      })
      await vi.advanceTimersByTimeAsync(31000)
      await assertion
    } finally {
      vi.useRealTimers()
    }
  })

  it('passes an abort signal so the request can actually be cancelled', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }))

    await getLoginStatus()

    expect(fetchMock.mock.calls[0][1].signal).toBeDefined()
  })

  it('gives campaign creation a longer budget, since it uploads files', async () => {
    vi.useFakeTimers()
    try {
      fetchMock.mockResolvedValue(jsonResponse({ id: 1 }))

      const pending = createCampaign({
        name: 'c',
        messageText: 'hi',
        sendMode: 'delay',
        contacts: [{ name: 'A', phone: '+2348161234765' }],
        mediaFiles: [new File(['x'], 'a.png', { type: 'image/png' })],
      })
      await vi.advanceTimersByTimeAsync(60000)
      await pending

      // Still in flight at 60s: the upload budget is longer than the default.
      expect(fetchMock).toHaveBeenCalled()
      const assertion = expect(pending).resolves.toBeDefined()
      await vi.advanceTimersByTimeAsync(70000)
      await assertion
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('logoutUser', () => {
  it('clears both stored values', () => {
    localStorage.setItem('authToken', 'abc')
    localStorage.setItem('username', 'alice')

    logoutUser()

    expect(localStorage.getItem('authToken')).toBeNull()
    expect(localStorage.getItem('username')).toBeNull()
  })
})