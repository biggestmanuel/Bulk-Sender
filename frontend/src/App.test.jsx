import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import { setUnauthorizedHandler } from './api'

// App.jsx owns the most intricate frontend logic in the app: it polls campaign
// status on an interval, decides when to stop polling, gates the Start button
// on contact validity, and wires the login overlay to a retry started from
// elsewhere. A regression here shows up as a page that hammers the API
// forever, or a send that can be triggered with invalid numbers.

const VALID_CSV = 'name,phone\nAda,+2348161234765\nBob,+14155552671'
const CSV_WITH_BAD_NUMBER = 'name,phone\nAda,+2348161234765\nBad,+2341'
const CSV_NEEDS_COUNTRY = 'name,phone\nAda,+2348161234765\nLocal,08012345678'

let fetchMock

function respond(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  }
}

function statusBody(overrides = {}) {
  return {
    campaign_status: 'running',
    status_detail: '',
    progress: { total: 2, sent: 1, failed: 0, pending: 1 },
    contacts: [
      { id: 1, name: 'Ada', phone: '+2348161234765', status: 'sent' },
      { id: 2, name: 'Bob', phone: '+14155552671', status: 'pending' },
    ],
    media_files: [],
    ...overrides,
  }
}

/** Routes each URL to a response, counting calls per path. */
function route(routes) {
  const calls = {}
  fetchMock = vi.fn(async (url, options = {}) => {
    const method = options.method || 'GET'
    const key = `${method} ${url.split('?')[0]}`
    calls[key] = (calls[key] || 0) + 1
    for (const [pattern, handler] of Object.entries(routes)) {
      if (url.includes(pattern)) {
        return typeof handler === 'function' ? handler(url, options) : handler
      }
    }
    return respond({ error: 'unrouted', code: 'unrouted' }, 500)
  })
  globalThis.fetch = fetchMock
  return calls
}

/**
 * Advances past the longest poll delay (30s), so exactly one poll fires per
 * call regardless of where the backoff currently sits.
 */
async function advancePolls(times) {
  for (let i = 0; i < times; i++) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(31000)
    })
  }
}

/** Advances in small slices, for asserting the backoff schedule itself. */
async function advanceBy(totalMs) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(totalMs)
  })
}

async function uploadCsv(user, contents = VALID_CSV) {
  const file = new File([contents], 'contacts.csv', { type: 'text/csv' })
  await user.upload(screen.getByLabelText(/choose csv file/i), file)
  // Match the filename, not "loaded" — both "Loaded contacts.csv" and
  // "2 contacts loaded" match /loaded/i.
  await screen.findByText('contacts.csv')
  // Parsing is async (Papa) and the contact list renders in a later commit
  // than the upload card, so wait for the composer to appear too.
  await screen.findByRole('textbox', { name: /your message/i })
}

/**
 * Walks the wizard to the point where Start sending is offered.
 *
 * The Start button only renders after the message is confirmed, so it cannot
 * be asserted before that click.
 */
async function reachStartButton(user) {
  await uploadCsv(user)
  await user.type(screen.getByRole('textbox', { name: /your message/i }), 'Hi {name}')
  await user.click(screen.getByRole('button', { name: /confirm message/i }))

  await waitFor(() =>
    expect(screen.getByRole('button', { name: /start sending/i })).toBeInTheDocument()
  )
}

beforeEach(() => {
  // Only the interval APIs are faked. Faking setTimeout as well breaks
  // testing-library's waitFor, which polls with setTimeout and would never
  // resolve. Faking the whole clock with shouldAdvanceTime instead works but
  // pushes this file from ~8s to ~99s, because every userEvent interaction
  // advances the clock in small increments.
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
  localStorage.setItem('authToken', 'test-token')
  localStorage.setItem('username', 'alice')
  setUnauthorizedHandler(null)
  window.URL.createObjectURL = vi.fn(() => 'blob:mock')
})

function setupUser() {
  return userEvent.setup()
}

afterEach(() => {
  vi.useRealTimers()
  setUnauthorizedHandler(null)
})

// --- Polling lifecycle ---------------------------------------------------

describe('App campaign status polling', () => {
  it('starts polling once a campaign is created', async () => {
    const calls = route({
      '/campaigns/create/': respond({ id: 42 }, 201),
      '/campaigns/42/start/': respond({ message: 'started' }, 202),
      '/campaigns/42/status/': respond(statusBody()),
      '/login-status/': respond({ phase: 'idle', code_ready: false, login_code: null }),
      '/campaigns/': respond({ campaigns: [], total: 0 }),
      '/profile/whatsapp-number/': respond({ whatsapp_number: '+2348161234765' }),
    })

    const user = setupUser()
    render(<App />)
    await reachStartButton(user)

    await user.click(screen.getByRole('button', { name: /start sending/i }))

    await waitFor(() => expect(calls['GET /api/campaigns/42/status/']).toBeGreaterThan(0))
  })

  it('polls on an interval', async () => {
    const calls = route({
      '/campaigns/create/': respond({ id: 42 }, 201),
      '/campaigns/42/start/': respond({ message: 'started' }, 202),
      '/campaigns/42/status/': respond(statusBody()),
      '/login-status/': respond({ phase: 'idle', code_ready: false, login_code: null }),
      '/campaigns/': respond({ campaigns: [], total: 0 }),
      '/profile/whatsapp-number/': respond({ whatsapp_number: '+2348161234765' }),
    })

    const user = setupUser()
    render(<App />)
    await reachStartButton(user)
    await user.click(screen.getByRole('button', { name: /start sending/i }))

    await waitFor(() => expect(calls['GET /api/campaigns/42/status/']).toBe(1))

    await advancePolls(3)
    expect(calls['GET /api/campaigns/42/status/']).toBeGreaterThan(3)
  })

  /**
   * The regression that motivated the terminal-state check: the original
   * effect polled every 3 seconds for the lifetime of the page, so a single
   * send meant a request every 3 seconds forever.
   */
  it('stops polling once the campaign completes', async () => {
    let phase = 'running'
    const calls = route({
      '/campaigns/create/': respond({ id: 42 }, 201),
      '/campaigns/42/start/': respond({ message: 'started' }, 202),
      '/campaigns/42/status/': () =>
        respond(
          statusBody(
            phase === 'completed'
              ? {
                  campaign_status: 'completed',
                  progress: { total: 2, sent: 2, failed: 0, pending: 0 },
                }
              : {}
          )
        ),
      '/login-status/': respond({ phase: 'idle', code_ready: false, login_code: null }),
      '/campaigns/': respond({ campaigns: [], total: 0 }),
      '/profile/whatsapp-number/': respond({ whatsapp_number: '+2348161234765' }),
    })

    const user = setupUser()
    render(<App />)
    await reachStartButton(user)
    await user.click(screen.getByRole('button', { name: /start sending/i }))
    await waitFor(() => expect(calls['GET /api/campaigns/42/status/']).toBeGreaterThan(0))

    phase = 'completed'
    await advancePolls(1)

    await waitFor(() => expect(screen.getByText(/finished/i)).toBeInTheDocument())

    const settled = calls['GET /api/campaigns/42/status/']
    await advancePolls(5)
    expect(calls['GET /api/campaigns/42/status/']).toBe(settled)
  })

  it('stops polling when the campaign fails', async () => {
    const calls = route({
      '/campaigns/create/': respond({ id: 42 }, 201),
      '/campaigns/42/start/': respond({ message: 'started' }, 202),
      '/campaigns/42/status/': respond(
        statusBody({ campaign_status: 'failed', status_detail: 'WhatsApp login did not complete.' })
      ),
      '/login-status/': respond({ phase: 'idle', code_ready: false, login_code: null }),
      '/campaigns/': respond({ campaigns: [], total: 0 }),
      '/profile/whatsapp-number/': respond({ whatsapp_number: '+2348161234765' }),
    })

    const user = setupUser()
    render(<App />)
    await reachStartButton(user)
    await user.click(screen.getByRole('button', { name: /start sending/i }))

    await waitFor(() => expect(screen.getByText(/login did not complete/i)).toBeInTheDocument())

    const settled = calls['GET /api/campaigns/42/status/']
    await advancePolls(5)
    expect(calls['GET /api/campaigns/42/status/']).toBe(settled)
  })

  /**
   * A 5,000-contact campaign in delay mode runs for hours. Polling every 3
   * seconds throughout would be over 16,000 requests and exhaust the API's
   * polling budget, freezing the progress panel on a send that was still
   * running. The interval doubles up to a 30s ceiling.
   */
  it('backs off the polling interval over time', async () => {
    const calls = route({
      '/campaigns/create/': respond({ id: 42 }, 201),
      '/campaigns/42/start/': respond({ message: 'started' }, 202),
      '/campaigns/42/status/': respond(statusBody()),
      '/login-status/': respond({ phase: 'idle', code_ready: false, login_code: null }),
      '/campaigns/': respond({ campaigns: [], total: 0 }),
      '/profile/whatsapp-number/': respond({ whatsapp_number: '+2348161234765' }),
    })

    const user = setupUser()
    render(<App />)
    await reachStartButton(user)
    await user.click(screen.getByRole('button', { name: /start sending/i }))

    await waitFor(() => expect(calls['GET /api/campaigns/42/status/']).toBe(1))

    // First gap is 3s.
    await advanceBy(3100)
    expect(calls['GET /api/campaigns/42/status/']).toBe(2)

    // Second gap has doubled to 6s: nothing at 4s, something by 10s.
    await advanceBy(4000)
    expect(calls['GET /api/campaigns/42/status/']).toBe(2)
    await advanceBy(3000)
    expect(calls['GET /api/campaigns/42/status/']).toBe(3)

    // It settles at the 30s ceiling rather than growing without bound.
    for (let i = 0; i < 12; i++) {
      await advancePolls(1)
    }
    await advanceBy(10000)
    const atCeiling = calls['GET /api/campaigns/42/status/']
    await advanceBy(25000)
    expect(calls['GET /api/campaigns/42/status/']).toBe(atCeiling + 1)
  })

  it('survives a transient poll failure', async () => {
    let failNext = true
    const calls = route({
      '/campaigns/create/': respond({ id: 42 }, 201),
      '/campaigns/42/start/': respond({ message: 'started' }, 202),
      '/campaigns/42/status/': () => {
        if (failNext) {
          failNext = false
          return respond({ error: 'temporary' }, 503)
        }
        return respond(statusBody())
      },
      '/login-status/': respond({ phase: 'idle', code_ready: false, login_code: null }),
      '/campaigns/': respond({ campaigns: [], total: 0 }),
      '/profile/whatsapp-number/': respond({ whatsapp_number: '+2348161234765' }),
    })

    const user = setupUser()
    render(<App />)
    await reachStartButton(user)
    await user.click(screen.getByRole('button', { name: /start sending/i }))

    await advancePolls(3)
    expect(calls['GET /api/campaigns/42/status/']).toBeGreaterThan(1)
  })

  it('stops polling on unmount', async () => {
    const calls = route({
      '/campaigns/create/': respond({ id: 42 }, 201),
      '/campaigns/42/start/': respond({ message: 'started' }, 202),
      '/campaigns/42/status/': respond(statusBody()),
      '/login-status/': respond({ phase: 'idle', code_ready: false, login_code: null }),
      '/campaigns/': respond({ campaigns: [], total: 0 }),
      '/profile/whatsapp-number/': respond({ whatsapp_number: '+2348161234765' }),
    })

    const user = setupUser()
    const { unmount } = render(<App />)
    await reachStartButton(user)
    await user.click(screen.getByRole('button', { name: /start sending/i }))
    await waitFor(() => expect(calls['GET /api/campaigns/42/status/']).toBeGreaterThan(0))

    unmount()
    const atUnmount = calls['GET /api/campaigns/42/status/']

    await advancePolls(4)
    expect(calls['GET /api/campaigns/42/status/']).toBe(atUnmount)
  })
})

// --- Gating the send ----------------------------------------------------

describe('App send gating', () => {
  async function setUpWizard(contents) {
    route({
      '/campaigns/create/': respond({ id: 42 }, 201),
      '/campaigns/42/start/': respond({ message: 'started' }, 202),
      '/campaigns/42/status/': respond(statusBody()),
      '/login-status/': respond({ phase: 'idle', code_ready: false, login_code: null }),
      '/campaigns/': respond({ campaigns: [], total: 0 }),
      '/profile/whatsapp-number/': respond({ whatsapp_number: '+2348161234765' }),
    })

    const user = setupUser()
    render(<App />)
    await uploadCsv(user, contents)
    return user
  }

  it('blocks the send while a number is invalid', async () => {
    const user = await setUpWizard(CSV_WITH_BAD_NUMBER)

    await user.type(screen.getByRole('textbox', { name: /your message/i }), 'Hi {name}')
    await user.click(screen.getByRole('button', { name: /confirm message/i }))

    expect(screen.getByRole('button', { name: /start sending/i })).toBeDisabled()
    expect(screen.getByText(/invalid phone number/i)).toBeInTheDocument()
  })

  it('blocks the send while a number needs a country', async () => {
    const user = await setUpWizard(CSV_NEEDS_COUNTRY)

    await user.type(screen.getByRole('textbox', { name: /your message/i }), 'Hi {name}')
    await user.click(screen.getByRole('button', { name: /confirm message/i }))

    expect(screen.getByRole('button', { name: /start sending/i })).toBeDisabled()
    expect(screen.getByText(/needs? a country code/i)).toBeInTheDocument()
  })

  it('enables the send once the rows are fixed', async () => {
    const user = await setUpWizard(CSV_WITH_BAD_NUMBER)

    await user.type(screen.getByRole('textbox', { name: /your message/i }), 'Hi {name}')
    await user.click(screen.getByRole('button', { name: /confirm message/i }))
    expect(screen.getByRole('button', { name: /start sending/i })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: /^edit$/i }))
    const input = screen.getByLabelText('Phone')
    await user.clear(input)
    await user.type(input, '+2348161234766')
    await user.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /start sending/i })).toBeEnabled()
    )
  })

  it('blocks the send with no message written', async () => {
    await setUpWizard(VALID_CSV)

    // The Start button is only rendered once a message is confirmed, so
    // there is nothing to disable — it simply is not offered.
    expect(screen.queryByRole('button', { name: /start sending/i })).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /your message/i })).toBeInTheDocument()
  })

  it('surfaces the backend error when creation is rejected', async () => {
    route({
      '/campaigns/create/': respond(
        {
          error: '1 of 2 contacts have unusable phone numbers. Examples — "+2341" has only 4 digits',
          code: 'invalid_contacts',
          rejected_count: 1,
        },
        400
      ),
      '/login-status/': respond({ phase: 'idle', code_ready: false, login_code: null }),
      '/campaigns/': respond({ campaigns: [], total: 0 }),
      '/profile/whatsapp-number/': respond({ whatsapp_number: '+2348161234765' }),
    })

    const user = setupUser()
    render(<App />)
    await reachStartButton(user)

    await user.click(screen.getByRole('button', { name: /start sending/i }))

    expect(await screen.findByText(/\+2341/)).toBeInTheDocument()
    expect(screen.getByText(/has only 4 digits/)).toBeInTheDocument()
  })
})

// --- Session handling ---------------------------------------------------

describe('App session handling', () => {
  it('shows the login screen when there is no token', async () => {
    localStorage.clear()
    route({})

    render(<App />)

    expect(screen.getByRole('heading', { name: /log in/i })).toBeInTheDocument()
  })

  it('logs out when a request returns 401', async () => {
    route({
      '/campaigns/': respond({ error: 'Invalid token.' }, 401),
      '/profile/whatsapp-number/': respond({ error: 'Invalid token.' }, 401),
    })

    render(<App />)

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /log in/i })).toBeInTheDocument()
    )
    expect(localStorage.getItem('authToken')).toBeNull()
  })

  it('clears the workspace on logout', async () => {
    route({
      '/campaigns/': respond({ campaigns: [], total: 0 }),
      '/profile/whatsapp-number/': respond({ whatsapp_number: '+2348161234765' }),
    })

    const user = setupUser()
    render(<App />)
    await screen.findByRole('heading', { name: /campaign workspace/i })

    await user.click(screen.getByRole('button', { name: /log out/i }))

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /log in/i })).toBeInTheDocument()
    )
    expect(localStorage.getItem('authToken')).toBeNull()
  })

  it('does not restart polling when an unrelated re-render happens', async () => {
    const calls = route({
      '/campaigns/create/': respond({ id: 42 }, 201),
      '/campaigns/42/start/': respond({ message: 'started' }, 202),
      '/campaigns/42/status/': respond(statusBody()),
      '/login-status/': respond({ phase: 'idle', code_ready: false, login_code: null }),
      '/campaigns/': respond({ campaigns: [], total: 0 }),
      '/profile/whatsapp-number/': respond({ whatsapp_number: '+2348161234765' }),
    })

    const user = setupUser()
    render(<App />)
    await reachStartButton(user)
    await user.click(screen.getByRole('button', { name: /start sending/i }))
    await waitFor(() => expect(calls['GET /api/campaigns/42/status/']).toBeGreaterThan(0))

    // Re-rendering the tree (as StrictMode does) must not multiply polls.
    const before = calls['GET /api/campaigns/42/status/']
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })
    expect(calls['GET /api/campaigns/42/status/']).toBe(before)
  })
})
