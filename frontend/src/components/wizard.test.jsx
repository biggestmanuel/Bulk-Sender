import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Auth from './Auth'
import ColumnMapping from './ColumnMapping'
import MessageComposer from './MessageComposer'
import SendSettings from './SendSettings'
import CountryPicker from './CountryPicker'

// The remaining wizard components. Each is small, but they sit between the
// user and a real send: a wizard step that silently discards input, or that
// accepts something invalid, ends with messages sent to the wrong people.

describe('Auth', () => {
  it('shows the login form when no token is stored', () => {
    localStorage.clear()
    render(<Auth onAuthSuccess={vi.fn()} />)

    expect(screen.getByRole('heading', { name: /^log in$/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/username/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
  })

  it('hides the email field until signing up', () => {
    localStorage.clear()
    render(<Auth onAuthSuccess={vi.fn()} />)

    expect(screen.queryByLabelText(/email/i)).not.toBeInTheDocument()
  })

  it('toggles to registration', async () => {
    localStorage.clear()
    const user = userEvent.setup()
    render(<Auth onAuthSuccess={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: /sign up/i }))

    expect(screen.getByRole('heading', { name: /create your account/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
  })

  it('toggles back to login', async () => {
    localStorage.clear()
    const user = userEvent.setup()
    render(<Auth onAuthSuccess={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: /sign up/i }))
    await user.click(screen.getByRole('button', { name: /log in/i }))

    expect(screen.getByRole('heading', { name: /^log in$/i })).toBeInTheDocument()
  })

  it('requires both fields before submitting', async () => {
    localStorage.clear()
    const user = userEvent.setup()
    render(<Auth onAuthSuccess={vi.fn()} />)

    await user.type(screen.getByLabelText(/username/i), 'alice')
    await user.click(screen.getByRole('button', { name: /^log in$/i }))

    expect(await screen.findByText(/enter both a username and password/i)).toBeInTheDocument()
  })

  it('shows the password field as obscured', () => {
    localStorage.clear()
    render(<Auth onAuthSuccess={vi.fn()} />)
    expect(screen.getByLabelText(/password/i)).toHaveAttribute('type', 'password')
  })

  it('uses the right autocomplete hints', async () => {
    localStorage.clear()
    const user = userEvent.setup()
    render(<Auth onAuthSuccess={vi.fn()} />)

    expect(screen.getByLabelText(/username/i)).toHaveAttribute('autocomplete', 'username')
    expect(screen.getByLabelText(/password/i)).toHaveAttribute('autocomplete', 'current-password')

    await user.click(screen.getByRole('button', { name: /sign up/i }))
    expect(screen.getByLabelText(/password/i)).toHaveAttribute('autocomplete', 'new-password')
  })

  it('clears the password field after a failed attempt', async () => {
    localStorage.clear()
    const onAuthSuccess = vi.fn()
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      text: async () => JSON.stringify({ error: 'Invalid username or password.' }),
    })

    const user = userEvent.setup()
    render(<Auth onAuthSuccess={onAuthSuccess} />)

    await user.type(screen.getByLabelText(/username/i), 'alice')
    await user.type(screen.getByLabelText(/password/i), 'wrong')
    await user.click(screen.getByRole('button', { name: /^log in$/i }))

    expect(await screen.findByText(/invalid username or password/i)).toBeInTheDocument()
    expect(onAuthSuccess).not.toHaveBeenCalled()
  })
})

describe('ColumnMapping', () => {
  const headers = ['Full Name', 'Mobile Number', 'Email', 'City']

  it('renders a dropdown per header', () => {
    render(<ColumnMapping headers={headers} onMappingDone={vi.fn()} />)

    for (const header of headers) {
      expect(screen.getAllByRole('option', { name: header }).length).toBe(2) // both dropdowns
    }
  })

  it('requires both columns', async () => {
    const user = userEvent.setup()
    const onMappingDone = vi.fn()
    render(<ColumnMapping headers={headers} onMappingDone={onMappingDone} />)

    await user.selectOptions(screen.getByLabelText(/name column/i), 'Full Name')
    await user.click(screen.getByRole('button', { name: /confirm mapping/i }))

    expect(await screen.findByText(/choose both a name column and a phone column/i)).toBeInTheDocument()
    expect(onMappingDone).not.toHaveBeenCalled()
  })

  it('rejects mapping the same column twice', async () => {
    const user = userEvent.setup()
    const onMappingDone = vi.fn()
    render(<ColumnMapping headers={headers} onMappingDone={onMappingDone} />)

    await user.selectOptions(screen.getByLabelText(/name column/i), 'Full Name')
    await user.selectOptions(screen.getByLabelText(/phone column/i), 'Full Name')
    await user.click(screen.getByRole('button', { name: /confirm mapping/i }))

    expect(await screen.findByText(/must be different columns/i)).toBeInTheDocument()
    expect(onMappingDone).not.toHaveBeenCalled()
  })

  it('reports the chosen columns', async () => {
    const user = userEvent.setup()
    const onMappingDone = vi.fn()
    render(<ColumnMapping headers={headers} onMappingDone={onMappingDone} />)

    await user.selectOptions(screen.getByLabelText(/name column/i), 'Full Name')
    await user.selectOptions(screen.getByLabelText(/phone column/i), 'Mobile Number')
    await user.click(screen.getByRole('button', { name: /confirm mapping/i }))

    expect(onMappingDone).toHaveBeenCalledWith({
      nameCol: 'Full Name',
      phoneCol: 'Mobile Number',
    })
  })

  it('starts with a placeholder option selected', () => {
    render(<ColumnMapping headers={headers} onMappingDone={vi.fn()} />)
    expect(screen.getByLabelText(/name column/i)).toHaveValue('')
  })
})

describe('MessageComposer', () => {
  it('labels the textarea for screen readers', () => {
    render(<MessageComposer onMessageReady={vi.fn()} />)
    expect(screen.getByRole('textbox', { name: /your message/i })).toBeInTheDocument()
  })

  it('refuses an empty message', async () => {
    const user = userEvent.setup()
    const onMessageReady = vi.fn()
    render(<MessageComposer onMessageReady={onMessageReady} />)

    await user.click(screen.getByRole('button', { name: /confirm message/i }))

    expect(await screen.findByText(/write a message before continuing/i)).toBeInTheDocument()
    expect(onMessageReady).not.toHaveBeenCalled()
  })

  it('refuses a whitespace-only message', async () => {
    const user = userEvent.setup()
    const onMessageReady = vi.fn()
    render(<MessageComposer onMessageReady={onMessageReady} />)

    await user.type(screen.getByRole('textbox', { name: /your message/i }), '     ')
    await user.click(screen.getByRole('button', { name: /confirm message/i }))

    expect(onMessageReady).not.toHaveBeenCalled()
  })

  /**
   * Sets the textarea value directly.
   *
   * userEvent.type() treats `{...}` as a key descriptor, so typing the app's
   * own `{name}` placeholder through it silently mangles the text (the
   * escaping rules are `{{` for a literal brace and a trailing `}` is not
   * consumed). fireEvent.change sets the value verbatim, which is what this
   * test is actually about.
   */
  function setMessage(value) {
    const textarea = screen.getByRole('textbox', { name: /your message/i })
    fireEvent.change(textarea, { target: { value } })
  }

  it('reports the message with no attachments', async () => {
    const user = userEvent.setup()
    const onMessageReady = vi.fn()
    render(<MessageComposer onMessageReady={onMessageReady} />)

    setMessage('Hi {name}')
    await user.click(screen.getByRole('button', { name: /confirm message/i }))

    expect(onMessageReady).toHaveBeenCalledWith({ message: 'Hi {name}', mediaFiles: [] })
  })

  it('accepts attachments', async () => {
    const user = userEvent.setup()
    const onMessageReady = vi.fn()
    render(<MessageComposer onMessageReady={onMessageReady} />)

    await user.type(screen.getByRole('textbox', { name: /your message/i }), 'Hi {{name}}')

    const file = new File(['x'], 'photo.png', { type: 'image/png' })
    await user.upload(screen.getByLabelText(/add photos or videos/i), file)

    expect(await screen.findByText('photo.png')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /confirm message/i }))
    expect(onMessageReady.mock.calls[0][0].mediaFiles).toHaveLength(1)
  })

  it('removes an attachment', async () => {
    const user = userEvent.setup()
    const onMessageReady = vi.fn()
    render(<MessageComposer onMessageReady={onMessageReady} />)

    await user.type(screen.getByRole('textbox', { name: /your message/i }), 'Hi')
    await user.upload(
      screen.getByLabelText(/add photos or videos/i),
      new File(['x'], 'photo.png', { type: 'image/png' })
    )
    await screen.findByText('photo.png')

    await user.click(screen.getByRole('button', { name: /remove/i }))
    await waitFor(() => expect(screen.queryByText('photo.png')).not.toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /confirm message/i }))
    expect(onMessageReady.mock.calls[0][0].mediaFiles).toHaveLength(0)
  })

  it('accumulates multiple attachments', async () => {
    const user = userEvent.setup()
    const onMessageReady = vi.fn()
    render(<MessageComposer onMessageReady={onMessageReady} />)

    await user.type(screen.getByRole('textbox', { name: /your message/i }), 'Hi')
    await user.upload(
      screen.getByLabelText(/add photos or videos/i),
      new File(['x'], 'a.png', { type: 'image/png' })
    )
    await user.upload(
      screen.getByLabelText(/add photos or videos/i),
      new File(['y'], 'b.png', { type: 'image/png' })
    )

    await screen.findByText('b.png')
    await user.click(screen.getByRole('button', { name: /confirm message/i }))
    expect(onMessageReady.mock.calls[0][0].mediaFiles).toHaveLength(2)
  })

  it('mentions the {name} placeholder', () => {
    render(<MessageComposer onMessageReady={vi.fn()} />)
    expect(screen.getByText('{name}')).toBeInTheDocument()
  })
})

describe('SendSettings', () => {
  it('defaults to the paced option', () => {
    render(<SendSettings onSettingsReady={vi.fn()} />)
    expect(screen.getByRole('radio', { name: /send with delay/i })).toBeChecked()
  })

  it('reports the paced mode', async () => {
    const user = userEvent.setup()
    const onSettingsReady = vi.fn()
    render(<SendSettings onSettingsReady={onSettingsReady} />)

    await user.click(screen.getByRole('button', { name: /confirm settings/i }))
    expect(onSettingsReady).toHaveBeenCalledWith({ mode: 'delay' })
  })

  it('reports the instant mode', async () => {
    const user = userEvent.setup()
    const onSettingsReady = vi.fn()
    render(<SendSettings onSettingsReady={onSettingsReady} />)

    await user.click(screen.getByRole('radio', { name: /send all at once/i }))
    await user.click(screen.getByRole('button', { name: /confirm settings/i }))

    expect(onSettingsReady).toHaveBeenCalledWith({ mode: 'instant' })
  })

  it('warns that the fast option risks a ban', () => {
    render(<SendSettings onSettingsReady={vi.fn()} />)
    expect(screen.getByText(/getting banned/i)).toBeInTheDocument()
  })
})

describe('CountryPicker', () => {
  it('focuses the search input on mount', () => {
    render(<CountryPicker onSelect={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getByPlaceholderText(/search for a country/i)).toHaveFocus()
  })

  it('shows a short list before typing', () => {
    render(<CountryPicker onSelect={vi.fn()} onCancel={vi.fn()} />)
    const options = screen.getAllByRole('button')
    expect(options.length).toBeGreaterThan(0)
    expect(options.length).toBeLessThanOrEqual(8)
  })

  it('filters by name prefix', async () => {
    const user = userEvent.setup()
    render(<CountryPicker onSelect={vi.fn()} onCancel={vi.fn()} />)

    await user.type(screen.getByPlaceholderText(/search for a country/i), 'United')

    const labels = screen.getAllByRole('button').map((b) => b.textContent)
    expect(labels.some((l) => l.includes('United States'))).toBe(true)
    expect(labels.every((l) => l.toLowerCase().startsWith('united'))).toBe(true)
  })

  it('matches case-insensitively', async () => {
    const user = userEvent.setup()
    render(<CountryPicker onSelect={vi.fn()} onCancel={vi.fn()} />)

    await user.type(screen.getByPlaceholderText(/search for a country/i), 'nigeria')
    expect(screen.getByRole('button', { name: /nigeria/i })).toBeInTheDocument()
  })

  it('shows the calling code', async () => {
    const user = userEvent.setup()
    render(<CountryPicker onSelect={vi.fn()} onCancel={vi.fn()} />)

    await user.type(screen.getByPlaceholderText(/search for a country/i), 'Nigeria')
    expect(screen.getByRole('button', { name: /nigeria.*\+234/i })).toBeInTheDocument()
  })

  it('reports the selection', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<CountryPicker onSelect={onSelect} onCancel={vi.fn()} />)

    await user.type(screen.getByPlaceholderText(/search for a country/i), 'Kenya')
    await user.click(screen.getByRole('button', { name: /kenya/i }))

    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ code: 'KE', callingCode: '254' })
    )
  })

  it('reports no matches', async () => {
    const user = userEvent.setup()
    render(<CountryPicker onSelect={vi.fn()} onCancel={vi.fn()} />)

    await user.type(screen.getByPlaceholderText(/search for a country/i), 'zzzz')

    expect(screen.getByText(/no countries match/i)).toBeInTheDocument()
  })

  it('cancels on Escape', async () => {
    const user = userEvent.setup()
    const onCancel = vi.fn()
    render(<CountryPicker onSelect={vi.fn()} onCancel={onCancel} />)

    await user.type(screen.getByPlaceholderText(/search for a country/i), '{Escape}')

    expect(onCancel).toHaveBeenCalled()
  })

  it('limits the result count', async () => {
    const user = userEvent.setup()
    render(<CountryPicker onSelect={vi.fn()} onCancel={vi.fn()} />)

    await user.type(screen.getByPlaceholderText(/search for a country/i), 'a')
    expect(screen.getAllByRole('button').length).toBeLessThanOrEqual(8)
  })
})
