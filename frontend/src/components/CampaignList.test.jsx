import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import CampaignList from './CampaignList'
import { setUnauthorizedHandler } from '../api'

let fetchMock
let handlerFor

function respond(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  }
}

function makeCampaign(overrides = {}) {
  return {
    id: 1,
    name: 'Campaign one',
    send_mode: 'delay',
    status: 'completed',
    status_detail: '',
    created_at: '2026-01-01T10:00:00Z',
    send_started_at: null,
    send_finished_at: null,
    total_contacts: 10,
    sent_contacts: 7,
    failed_contacts: 3,
    pending_contacts: 0,
    media_count: 0,
    can_retry: true,
    ...overrides,
  }
}

function listResponse(campaigns, extra = {}) {
  return respond({
    campaigns,
    page: 1,
    page_size: 10,
    total: campaigns.length,
    has_next: false,
    ...extra,
  })
}

beforeEach(() => {
  handlerFor = {}
  fetchMock = vi.fn(async (url, options = {}) => {
    const method = options.method || 'GET'
    handlerFor[method] = handlerFor[method] || []
    handlerFor[method].push({ url, options })

    if (url.includes('/retry/')) return respond({ reset_count: 3, campaign_id: 1 })
    if (method === 'DELETE') return respond({ message: 'deleted' })
    return listResponse([makeCampaign()])
  })
  globalThis.fetch = fetchMock
  setUnauthorizedHandler(null)
  window.confirm = vi.fn(() => true)
})

afterEach(() => {
  setUnauthorizedHandler(null)
})

describe('CampaignList loading', () => {
  it('renders campaigns from the API', async () => {
    render(<CampaignList />)

    expect(await screen.findByText('Campaign one')).toBeInTheDocument()
    expect(screen.getByText('7 sent')).toBeInTheDocument()
    expect(screen.getByText('of 10')).toBeInTheDocument()
  })

  it('shows an empty state', async () => {
    fetchMock.mockResolvedValue(listResponse([]))
    render(<CampaignList />)

    expect(await screen.findByText(/no campaigns yet/i)).toBeInTheDocument()
  })

  it('surfaces a load error instead of rendering nothing', async () => {
    fetchMock.mockResolvedValue(respond({ error: 'Server unavailable' }, 500))
    render(<CampaignList />)

    expect(await screen.findByText(/server unavailable/i)).toBeInTheDocument()
  })

  it('shows the total count', async () => {
    fetchMock.mockResolvedValue(
      listResponse([makeCampaign(), makeCampaign({ id: 2, name: 'Second' })], { total: 14 })
    )
    render(<CampaignList />)

    expect(await screen.findByText(/your campaigns \(14\)/i)).toBeInTheDocument()
  })

  it('labels fast campaigns distinctly from paced ones', async () => {
    fetchMock.mockResolvedValue(
      listResponse([
        makeCampaign({ id: 1, name: 'Paced', send_mode: 'delay' }),
        makeCampaign({ id: 2, name: 'Fast', send_mode: 'instant' }),
      ])
    )
    render(<CampaignList />)

    await screen.findByText('Paced')
    expect(screen.getByText('Fast')).toBeInTheDocument()
  })

  it('reports the attachment count when present', async () => {
    fetchMock.mockResolvedValue(listResponse([makeCampaign({ media_count: 3 })]))
    render(<CampaignList />)

    expect(await screen.findByText(/3 attachment/i)).toBeInTheDocument()
  })
})

describe('CampaignList retry', () => {
  it('offers retry only when the backend says it can', async () => {
    render(<CampaignList />)

    await screen.findByText('Campaign one')
    expect(screen.getByRole('button', { name: /retry 3/i })).toBeInTheDocument()
  })

  it('hides retry when nothing failed', async () => {
    fetchMock.mockResolvedValue(
      listResponse([makeCampaign({ failed_contacts: 0, can_retry: false })])
    )
    render(<CampaignList />)

    await screen.findByText('Campaign one')
    expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument()
  })

  /**
   * Retry is deliberately two calls: reset the rows, then start. The start goes
   * through the existing guards, so the user sees real errors if they're busy
   * or over quota.
   */
  it('resets then starts', async () => {
    const user = userEvent.setup()
    render(<CampaignList />)

    await user.click(await screen.findByRole('button', { name: /retry 3/i }))

    await waitFor(() => expect(handlerFor.POST).toBeDefined())
    // Order matters: the reset must complete before the send starts.
    expect(handlerFor.POST).toHaveLength(2)
    expect(handlerFor.POST[0].url).toContain('/retry/')
    expect(handlerFor.POST[1].url).toContain('/start/')
  })

  it('does not start if the reset reports nothing to do', async () => {
    fetchMock.mockImplementation(async (url, reqOptions = {}) => {
      const method = reqOptions.method || 'GET'
      handlerFor[method] = handlerFor[method] || []
      handlerFor[method].push({ url, options: reqOptions })

      if (url.includes('/retry/')) return respond({ reset_count: 0, campaign_id: 1 })
      return listResponse([makeCampaign()])
    })

    const user = userEvent.setup()
    render(<CampaignList />)

    await user.click(await screen.findByRole('button', { name: /retry 3/i }))

    await screen.findByText(/nothing left to retry/i)
    const retryCalls = (handlerFor.POST || []).filter((call) => call.url.includes('/retry/'))
    const startCalls = (handlerFor.POST || []).filter((call) => call.url.includes('/start/'))
    expect(retryCalls).toHaveLength(1)
    expect(startCalls).toHaveLength(0)
  })

  it('notifies the parent so progress can be shown', async () => {
    const user = userEvent.setup()
    const onCampaignRestarted = vi.fn()
    render(<CampaignList onCampaignRestarted={onCampaignRestarted} />)

    await user.click(await screen.findByRole('button', { name: /retry 3/i }))

    await waitFor(() => expect(onCampaignRestarted).toHaveBeenCalledWith(1))
  })

  it('explains that nothing was left to retry', async () => {
    fetchMock.mockImplementation(async (url) => {
      if (url.includes('/retry/')) return respond({ reset_count: 0, campaign_id: 1 })
      return listResponse([makeCampaign()])
    })

    const user = userEvent.setup()
    render(<CampaignList />)

    await user.click(await screen.findByRole('button', { name: /retry 3/i }))

    expect(await screen.findByText(/nothing left to retry/i)).toBeInTheDocument()
  })

  it('surfaces a failed start', async () => {
    fetchMock.mockImplementation(async (url) => {
      if (url.includes('/retry/')) return respond({ reset_count: 3 })
      if (url.includes('/start/')) {
        return respond({ error: 'You already have a campaign sending.' }, 409)
      }
      return listResponse([makeCampaign()])
    })

    const user = userEvent.setup()
    render(<CampaignList />)

    await user.click(await screen.findByRole('button', { name: /retry 3/i }))

    expect(await screen.findByText(/already have a campaign sending/i)).toBeInTheDocument()
  })
})

describe('CampaignList delete', () => {
  it('asks for confirmation before deleting', async () => {
    const user = userEvent.setup()
    render(<CampaignList />)

    await screen.findByText('Campaign one')
    await user.click(screen.getByRole('button', { name: /delete/i }))

    expect(window.confirm).toHaveBeenCalled()
    expect(window.confirm.mock.calls[0][0]).toMatch(/permanently removes/i)
  })

  it('does nothing when confirmation is declined', async () => {
    window.confirm = vi.fn(() => false)
    const user = userEvent.setup()
    render(<CampaignList />)

    await screen.findByText('Campaign one')
    await user.click(screen.getByRole('button', { name: /delete/i }))

    expect(handlerFor.DELETE).toBeUndefined()
  })

  it('deletes after confirmation', async () => {
    const user = userEvent.setup()
    render(<CampaignList />)

    await screen.findByText('Campaign one')
    await user.click(screen.getByRole('button', { name: /delete/i }))

    await waitFor(() => expect(handlerFor.DELETE).toBeDefined())
    expect(handlerFor.DELETE[0].options.method).toBe('DELETE')
  })

  it('surfaces a delete error', async () => {
    fetchMock.mockImplementation(async (url, reqOptions = {}) => {
      if ((reqOptions.method || 'GET') === 'DELETE') {
        return respond({ error: 'This campaign is still sending.' }, 409)
      }
      return listResponse([makeCampaign()])
    })

    const user = userEvent.setup()
    render(<CampaignList />)

    await screen.findByText('Campaign one')
    await user.click(screen.getByRole('button', { name: /delete/i }))

    expect(await screen.findByText(/still sending/i)).toBeInTheDocument()
  })
})

describe('CampaignList pagination', () => {
  it('hides paging when everything fits', async () => {
    render(<CampaignList />)

    await screen.findByText('Campaign one')
    expect(screen.queryByRole('button', { name: /older/i })).not.toBeInTheDocument()
  })

  it('offers Older when more pages exist', async () => {
    fetchMock.mockResolvedValue(
      listResponse([makeCampaign()], { has_next: true, total: 40 })
    )
    render(<CampaignList />)

    await screen.findByText('Campaign one')
    const older = screen.getByRole('button', { name: /older/i })
    expect(older).toBeEnabled()
  })

  it('requests the next page', async () => {
    const user = userEvent.setup()
    fetchMock.mockResolvedValue(
      listResponse([makeCampaign()], { has_next: true, total: 40 })
    )
    render(<CampaignList />)

    await user.click(await screen.findByRole('button', { name: /older/i }))

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => url.includes('page=2'))
      ).toBe(true)
    )
  })
})