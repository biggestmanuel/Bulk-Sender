import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import WhatsAppNumberSettings from './WhatsAppNumberSettings'
import { setUnauthorizedHandler } from '../api'

let fetchMock
let putBodies
let currentNumber

function respond(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  }
}

beforeEach(() => {
  currentNumber = ''
  putBodies = []
  fetchMock = vi.fn(async (url, options = {}) => {
    const method = options.method || 'GET'
    if (method === 'PUT') {
      putBodies.push(JSON.parse(options.body))
      currentNumber = options.body && JSON.parse(options.body).whatsapp_number
      return respond({ whatsapp_number: currentNumber })
    }
    return respond({ whatsapp_number: currentNumber })
  })
  globalThis.fetch = fetchMock
  setUnauthorizedHandler(null)
})

afterEach(() => {
  setUnauthorizedHandler(null)
})

async function startEditing(user) {
  await user.click(await screen.findByRole('button', { name: 'Add' }))
  return screen.getByLabelText('WhatsApp phone number')
}

describe('WhatsAppNumberSettings display', () => {
  it('shows the saved number', async () => {
    currentNumber = '+2348161234765'
    render(<WhatsAppNumberSettings />)

    expect(await screen.findByText('+2348161234765')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Change' })).toBeInTheDocument()
  })

  it('flags a missing number', async () => {
    render(<WhatsAppNumberSettings />)

    expect(await screen.findByText('not set')).toBeInTheDocument()
  })

  it('still offers the editor when loading the number fails', async () => {
    // Worst case the user sees "not set" and can set it, which surfaces the
    // real problem then. It must not render an empty header forever.
    fetchMock.mockResolvedValue(respond({ error: 'Server error' }, 500))

    render(<WhatsAppNumberSettings />)

    expect(await screen.findByRole('button', { name: 'Add' })).toBeInTheDocument()
  })
})

describe('WhatsAppNumberSettings validation', () => {
  it('rejects an empty value', async () => {
    const user = userEvent.setup()
    render(<WhatsAppNumberSettings />)
    await startEditing(user)

    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText(/enter a phone number/i)).toBeInTheDocument()
    expect(putBodies).toHaveLength(0)
  })

  it('rejects a number with no country code before hitting the network', async () => {
    const user = userEvent.setup()
    render(<WhatsAppNumberSettings />)
    const input = await startEditing(user)

    await user.clear(input)
    await user.type(input, '08012345678')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText(/include your country code/i)).toBeInTheDocument()
    expect(putBodies).toHaveLength(0)
  })

  it('rejects an invalid number', async () => {
    const user = userEvent.setup()
    render(<WhatsAppNumberSettings />)
    const input = await startEditing(user)

    await user.clear(input)
    await user.type(input, '+2341')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText(/does not look like a valid phone number/i)).toBeInTheDocument()
    expect(putBodies).toHaveLength(0)
  })
})

describe('WhatsAppNumberSettings saving', () => {
  it('saves and displays the normalized number', async () => {
    const user = userEvent.setup()
    render(<WhatsAppNumberSettings />)
    const input = await startEditing(user)

    await user.clear(input)
    await user.type(input, '+2348161234765')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(screen.queryByLabelText('WhatsApp phone number')).not.toBeInTheDocument())
    expect(screen.getByText('+2348161234765')).toBeInTheDocument()
  })

  it('sends the draft value', async () => {
    const user = userEvent.setup()
    render(<WhatsAppNumberSettings />)
    const input = await startEditing(user)

    await user.clear(input)
    await user.type(input, '+14155552671')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(putBodies).toHaveLength(1))
    expect(putBodies[0]).toEqual({ whatsapp_number: '+14155552671' })
  })

  /**
   * Regression: the editor previously closed unconditionally, so a rejected
   * save discarded the number the user had just typed with no explanation.
   */
  it('keeps the editor open and preserves input when the server rejects it', async () => {
    fetchMock.mockImplementation(async (url, options = {}) => {
      if ((options.method || 'GET') === 'PUT') {
        return respond({ error: 'That does not look like a valid phone number.' }, 400)
      }
      return respond({ whatsapp_number: '' })
    })

    const user = userEvent.setup()
    render(<WhatsAppNumberSettings />)
    const input = await startEditing(user)

    await user.clear(input)
    await user.type(input, '+2348161234765')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText(/does not look like a valid phone number/i)).toBeInTheDocument()
    expect(screen.getByLabelText('WhatsApp phone number')).toHaveValue('+2348161234765')
  })

  it('saves on Enter', async () => {
    const user = userEvent.setup()
    render(<WhatsAppNumberSettings />)
    const input = await startEditing(user)

    await user.clear(input)
    await user.type(input, '+14155552671{Enter}')

    await waitFor(() => expect(screen.getByText('+14155552671')).toBeInTheDocument())
  })

  it('closes on Escape without saving', async () => {
    const user = userEvent.setup()
    render(<WhatsAppNumberSettings />)
    const input = await startEditing(user)

    await user.clear(input)
    await user.type(input, '+14155552671{Escape}')

    await waitFor(() => expect(screen.queryByLabelText('WhatsApp phone number')).not.toBeInTheDocument())
    expect(putBodies).toHaveLength(0)
    expect(screen.getByText('not set')).toBeInTheDocument()
  })
})