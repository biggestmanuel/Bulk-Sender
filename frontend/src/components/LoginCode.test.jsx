import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import LoginCode from './LoginCode'
import { setUnauthorizedHandler } from '../api'

let fetchMock
let statuses

function respondWith(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  }
}

const IDLE = {
  phase: 'idle',
  code_ready: false,
  login_code: null,
  login_slow: false,
  login_failed: false,
  login_code_error: null,
}

const WITH_CODE = {
  ...IDLE,
  phase: 'waiting_for_code',
  code_ready: true,
  login_code: 'ABCD1234',
}

beforeEach(() => {
  statuses = []
  fetchMock = vi.fn(async () => respondWith(statuses.length ? statuses[0] : IDLE))
  globalThis.fetch = fetchMock
  setUnauthorizedHandler(null)
})

afterEach(() => {
  vi.useRealTimers()
})

/**
 * Drives the poll loop by making the next response a specific payload.
 * Simpler and less brittle than advancing fake timers, since the component
 * polls immediately on mount.
 */
function nextResponse(body, status = 200) {
  fetchMock.mockImplementation(async () => respondWith(body, status))
}

describe('LoginCode visibility', () => {
  it('renders nothing while no code has been generated', async () => {
    render(<LoginCode onReady={() => {}} onFailed={() => {}} />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows the code once the backend has one', async () => {
    nextResponse(WITH_CODE)
    render(<LoginCode onReady={() => {}} onFailed={() => {}} />)

    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('ABCD1234')).toBeInTheDocument()
  })

  it('warns about a slow connection', async () => {
    nextResponse({ ...WITH_CODE, login_slow: true })
    render(<LoginCode onReady={() => {}} onFailed={() => {}} />)

    expect(await screen.findByText(/taking a while/i)).toBeInTheDocument()
  })

  it('surfaces a backend error instead of a blank overlay', async () => {
    nextResponse({ ...IDLE, login_code_error: 'WhatsApp changed their login page' })
    render(<LoginCode onReady={() => {}} onFailed={() => {}} />)

    expect(await screen.findByText(/couldn't generate a login code/i)).toBeInTheDocument()
    expect(screen.getByText('WhatsApp changed their login page')).toBeInTheDocument()
  })

  it('lets the user dismiss an unrecoverable error', async () => {
    const user = (await import('@testing-library/user-event')).default.setup()
    nextResponse({ ...IDLE, login_code_error: 'boom' })
    render(<LoginCode onReady={() => {}} onFailed={() => {}} />)

    await user.click(await screen.findByRole('button', { name: /dismiss/i }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

/**
 * Advances past one poll interval.
 *
 * The component polls every 2s by design, so any test that needs a *second*
 * poll has to either wait two real seconds or drive the clock. Fake timers are
 * used throughout: real waiting would make this file take half a minute and
 * still be timing-dependent.
 */
async function tick(times = 1) {
  for (let i = 0; i < times; i++) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100)
    })
  }
}

describe('LoginCode terminal transitions', () => {
  it('calls onReady when the code disappears after being seen', async () => {
    vi.useFakeTimers()
    const onReady = vi.fn()
    const onFailed = vi.fn()

    nextResponse(WITH_CODE)
    render(<LoginCode onReady={onReady} onFailed={onFailed} />)

    await act(async () => {})
    expect(screen.getByText('ABCD1234')).toBeInTheDocument()

    nextResponse(IDLE)
    await tick()

    expect(onReady).toHaveBeenCalledOnce()
    expect(onFailed).not.toHaveBeenCalled()
  })

  it('calls onFailed when the backend reports failure', async () => {
    const onReady = vi.fn()
    const onFailed = vi.fn()

    nextResponse({ ...IDLE, login_failed: true })
    render(<LoginCode onReady={onReady} onFailed={onFailed} />)

    await waitFor(() => expect(onFailed).toHaveBeenCalledOnce())
    expect(onReady).not.toHaveBeenCalled()
  })

  /**
   * The old component inferred success from a code disappearing. If the very
   * first poll happens after the device is already linked, it never saw a code
   * and would sit there forever. The backend's explicit state is the fix.
   */
  it('does not call onReady when no code was ever seen', async () => {
    const onReady = vi.fn()
    nextResponse(IDLE)
    render(<LoginCode onReady={onReady} onFailed={() => {}} />)

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(onReady).not.toHaveBeenCalled()
  })

  it('never fires both callbacks', async () => {
    const onReady = vi.fn()
    const onFailed = vi.fn()

    nextResponse({ ...IDLE, login_failed: true })
    const { rerender } = render(<LoginCode onReady={onReady} onFailed={onFailed} />)

    await waitFor(() => expect(onFailed).toHaveBeenCalled())
    rerender(<LoginCode onReady={onReady} onFailed={onFailed} />)
    expect(onReady).not.toHaveBeenCalled()
  })
})

describe('LoginCode polling lifecycle', () => {
  it('stops polling once resolved', async () => {
    vi.useFakeTimers()
    const onFailed = vi.fn()
    nextResponse({ ...IDLE, login_failed: true })
    render(<LoginCode onReady={() => {}} onFailed={onFailed} />)

    await act(async () => {})
    expect(onFailed).toHaveBeenCalled()

    const callsAtResolve = fetchMock.mock.calls.length
    await tick(3)
    expect(fetchMock.mock.calls.length).toBe(callsAtResolve)
  })

  it('stops polling on unmount', async () => {
    vi.useFakeTimers()
    const { unmount } = render(<LoginCode onReady={() => {}} onFailed={() => {}} />)

    await act(async () => {})
    expect(fetchMock).toHaveBeenCalled()
    unmount()

    const callsAtUnmount = fetchMock.mock.calls.length
    await tick(3)
    expect(fetchMock.mock.calls.length).toBe(callsAtUnmount)
  })

  /**
   * The effect listed onReady/onFailed as dependencies while the parent
   * recreated them every render, so the interval was rebuilt several times a
   * second for the whole login window. A re-render must not add polls.
   */
  it('does not restart polling when the parent re-renders with new callbacks', async () => {
    function Parent() {
      return <LoginCode onReady={() => {}} onFailed={() => {}} />
    }

    const { rerender } = render(<Parent />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const before = fetchMock.mock.calls.length

    for (let i = 0; i < 5; i++) {
      rerender(<Parent />)
    }

    expect(fetchMock.mock.calls.length).toBe(before)
  })

  it('survives a transient network error', async () => {
    vi.useFakeTimers()
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))

    render(<LoginCode onReady={() => {}} onFailed={() => {}} />)

    await act(async () => {})
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // Still mounted and polling rather than blowing up.
    await tick()
    expect(fetchMock.mock.calls.length).toBeGreaterThan(1)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('treats an expired session as a failure rather than looping forever', async () => {
    vi.useFakeTimers()
    localStorage.setItem('authToken', 'stale')
    const onFailed = vi.fn()

    // A real 401 response, not a thrown error: the api client distinguishes
    // an HTTP 401 from a network-level failure, and only the former should
    // end the login flow.
    fetchMock.mockImplementation(async () =>
      respondWith({ error: 'Invalid token.' }, 401)
    )

    render(<LoginCode onReady={() => {}} onFailed={onFailed} />)

    await act(async () => {})
    expect(onFailed).toHaveBeenCalled()
    // The dead token is cleared so the app doesn't keep retrying with it.
    expect(localStorage.getItem('authToken')).toBeNull()
  })

  it('keeps polling on a network failure, which is not an auth problem', async () => {
    vi.useFakeTimers()
    const onFailed = vi.fn()

    fetchMock.mockImplementation(async () => {
      throw new TypeError('Failed to fetch')
    })

    render(<LoginCode onReady={() => {}} onFailed={onFailed} />)

    await act(async () => {})
    await tick()

    expect(onFailed).not.toHaveBeenCalled()
    expect(fetchMock.mock.calls.length).toBeGreaterThan(1)
  })
})

describe('LoginCode accessibility', () => {
  it('exposes the dialog with a label', async () => {
    nextResponse(WITH_CODE)
    render(<LoginCode onReady={() => {}} onFailed={() => {}} />)

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveAccessibleName(/enter this code/i)
  })
})