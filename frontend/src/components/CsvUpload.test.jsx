import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import CsvUpload from './CsvUpload'

// CsvUpload parses with Papa. The component was written to replace a
// hand-rolled split('\n')/split(','), which broke on quoted commas and CRLF
// endings — those are exactly the cases worth pinning, since a spreadsheet
// export is exactly what this tool receives.

function makeFile(contents, name = 'contacts.csv') {
  return new File([contents], name, { type: 'text/csv' })
}

describe('CsvUpload parsing', () => {
  it('reports the parsed headers and rows', async () => {
    const user = userEvent.setup()
    const onContactsLoaded = vi.fn()
    render(<CsvUpload onContactsLoaded={onContactsLoaded} />)

    await user.upload(
      screen.getByLabelText(/choose csv file/i),
      makeFile('name,phone\nAda,+2348161234765\nBob,+14155552671')
    )

    await waitFor(() => expect(onContactsLoaded).toHaveBeenCalled())

    const parsed = onContactsLoaded.mock.calls[0][0]
    expect(parsed.headers).toEqual(['name', 'phone'])
    expect(parsed.dataRows).toEqual([
      ['Ada', '+2348161234765'],
      ['Bob', '+14155552671'],
    ])
  })

  it('handles commas inside quoted fields', async () => {
    const user = userEvent.setup()
    const onContactsLoaded = vi.fn()
    render(<CsvUpload onContactsLoaded={onContactsLoaded} />)

    await user.upload(
      screen.getByLabelText(/choose csv file/i),
      makeFile('name,phone\n"Doe, Ada",+2348161234765')
    )

    await waitFor(() => expect(onContactsLoaded).toHaveBeenCalled())
    const parsed = onContactsLoaded.mock.calls[0][0]
    expect(parsed.dataRows[0][0]).toBe('Doe, Ada')
  })

  it('handles Windows line endings', async () => {
    const user = userEvent.setup()
    const onContactsLoaded = vi.fn()
    render(<CsvUpload onContactsLoaded={onContactsLoaded} />)

    await user.upload(
      screen.getByLabelText(/choose csv file/i),
      makeFile('name,phone\r\nAda,+2348161234765\r\n')
    )

    await waitFor(() => expect(onContactsLoaded).toHaveBeenCalled())
    const parsed = onContactsLoaded.mock.calls[0][0]
    expect(parsed.headers).toEqual(['name', 'phone'])
    expect(parsed.dataRows).toEqual([['Ada', '+2348161234765']])
  })

  it('trims whitespace around headers and cells', async () => {
    const user = userEvent.setup()
    const onContactsLoaded = vi.fn()
    render(<CsvUpload onContactsLoaded={onContactsLoaded} />)

    await user.upload(
      screen.getByLabelText(/choose csv file/i),
      makeFile(' name , phone \n Ada , +2348161234765 ')
    )

    await waitFor(() => expect(onContactsLoaded).toHaveBeenCalled())
    const parsed = onContactsLoaded.mock.calls[0][0]
    expect(parsed.headers).toEqual(['name', 'phone'])
    expect(parsed.dataRows).toEqual([['Ada', '+2348161234765']])
  })

  it('skips empty lines', async () => {
    const user = userEvent.setup()
    const onContactsLoaded = vi.fn()
    render(<CsvUpload onContactsLoaded={onContactsLoaded} />)

    await user.upload(
      screen.getByLabelText(/choose csv file/i),
      makeFile('name,phone\nAda,+2348161234765\n\n\nBob,+14155552671')
    )

    await waitFor(() => expect(onContactsLoaded).toHaveBeenCalled())
    expect(onContactsLoaded.mock.calls[0][0].dataRows).toHaveLength(2)
  })

  it('converts missing cells to empty strings, not undefined', async () => {
    const user = userEvent.setup()
    const onContactsLoaded = vi.fn()
    render(<CsvUpload onContactsLoaded={onContactsLoaded} />)

    await user.upload(
      screen.getByLabelText(/choose csv file/i),
      makeFile('name,phone,note\nAda,,hello')
    )

    await waitFor(() => expect(onContactsLoaded).toHaveBeenCalled())
    const row = onContactsLoaded.mock.calls[0][0].dataRows[0]
    expect(row).toEqual(['Ada', '', 'hello'])
  })

  it('surfaces a file with no data rows', async () => {
    const user = userEvent.setup()
    const onContactsLoaded = vi.fn()
    render(<CsvUpload onContactsLoaded={onContactsLoaded} />)

    await user.upload(screen.getByLabelText(/choose csv file/i), makeFile('name,phone'))

    expect(await screen.findByText(/empty or is missing a header row/i)).toBeInTheDocument()
    expect(onContactsLoaded).not.toHaveBeenCalled()
  })

  it('surfaces a completely empty file', async () => {
    const user = userEvent.setup()
    const onContactsLoaded = vi.fn()
    render(<CsvUpload onContactsLoaded={onContactsLoaded} />)

    await user.upload(screen.getByLabelText(/choose csv file/i), makeFile(''))

    expect(await screen.findByText(/looks empty/i)).toBeInTheDocument()
    expect(onContactsLoaded).not.toHaveBeenCalled()
  })

  it('shows the filename once loaded', async () => {
    const user = userEvent.setup()
    render(<CsvUpload onContactsLoaded={vi.fn()} />)

    await user.upload(
      screen.getByLabelText(/choose csv file/i),
      makeFile('name,phone\nAda,+2348161234765', 'my-contacts.csv')
    )

    expect(await screen.findByText('my-contacts.csv')).toBeInTheDocument()
  })

  it('accepts only .csv in the file picker', () => {
    render(<CsvUpload onContactsLoaded={vi.fn()} />)
    expect(screen.getByLabelText(/choose csv file/i)).toHaveAttribute('accept', '.csv')
  })
})

describe('CsvUpload merge vs replace', () => {
  it('does not ask when there are no existing contacts', async () => {
    const user = userEvent.setup()
    const onContactsLoaded = vi.fn()
    render(<CsvUpload onContactsLoaded={onContactsLoaded} hasExistingContacts={false} />)

    await user.upload(
      screen.getByLabelText(/choose csv file/i),
      makeFile('name,phone\nAda,+2348161234765')
    )

    await waitFor(() => expect(onContactsLoaded).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: /merge with existing/i })).not.toBeInTheDocument()
    expect(onContactsLoaded.mock.calls[0][1]).toEqual({ mode: 'replace' })
  })

  it('asks when contacts are already loaded', async () => {
    const user = userEvent.setup()
    const onContactsLoaded = vi.fn()
    render(<CsvUpload onContactsLoaded={onContactsLoaded} hasExistingContacts />)

    await user.upload(
      screen.getByLabelText(/choose csv file/i),
      makeFile('name,phone\nAda,+2348161234765')
    )

    expect(await screen.findByRole('button', { name: /merge with existing/i })).toBeInTheDocument()
    // Held back until the user decides.
    expect(onContactsLoaded).not.toHaveBeenCalled()
  })

  it('hands back merge when chosen', async () => {
    const user = userEvent.setup()
    const onContactsLoaded = vi.fn()
    render(<CsvUpload onContactsLoaded={onContactsLoaded} hasExistingContacts />)

    await user.upload(
      screen.getByLabelText(/choose csv file/i),
      makeFile('name,phone\nAda,+2348161234765')
    )
    await user.click(await screen.findByRole('button', { name: /merge with existing/i }))

    await waitFor(() => expect(onContactsLoaded).toHaveBeenCalled())
    expect(onContactsLoaded.mock.calls[0][1]).toEqual({ mode: 'merge' })
  })

  it('hands back replace when chosen', async () => {
    const user = userEvent.setup()
    const onContactsLoaded = vi.fn()
    render(<CsvUpload onContactsLoaded={onContactsLoaded} hasExistingContacts />)

    await user.upload(
      screen.getByLabelText(/choose csv file/i),
      makeFile('name,phone\nAda,+2348161234765')
    )
    await user.click(await screen.findByRole('button', { name: /replace existing/i }))

    await waitFor(() => expect(onContactsLoaded).toHaveBeenCalled())
    expect(onContactsLoaded.mock.calls[0][1]).toEqual({ mode: 'replace' })
  })

  it('cancels without loading anything', async () => {
    const user = userEvent.setup()
    const onContactsLoaded = vi.fn()
    render(<CsvUpload onContactsLoaded={onContactsLoaded} hasExistingContacts />)

    await user.upload(
      screen.getByLabelText(/choose csv file/i),
      makeFile('name,phone\nAda,+2348161234765')
    )
    await user.click(await screen.findByRole('button', { name: /cancel/i }))

    expect(onContactsLoaded).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /merge with existing/i })).not.toBeInTheDocument()
    )
  })

  it('names the pending file in the prompt', async () => {
    const user = userEvent.setup()
    render(<CsvUpload onContactsLoaded={vi.fn()} hasExistingContacts />)

    await user.upload(
      screen.getByLabelText(/choose csv file/i),
      makeFile('name,phone\nAda,+2348161234765', 'extra-contacts.csv')
    )

    expect(await screen.findByText('extra-contacts.csv')).toBeInTheDocument()
  })
})

describe('CsvUpload drag and drop', () => {
  let fireEvent

  beforeEach(async () => {
    const mod = await import('@testing-library/dom')
    fireEvent = mod.fireEvent
  })

  function dropFile(zone, file) {
    fireEvent.dragOver(zone, { dataTransfer: { files: [file], types: ['Files'] } })
    fireEvent.drop(zone, { dataTransfer: { files: [file] } })
  }

  it('parses a dropped file', async () => {
    const onContactsLoaded = vi.fn()
    const { container } = render(<CsvUpload onContactsLoaded={onContactsLoaded} />)

    dropFile(container.firstChild, makeFile('name,phone\nAda,+2348161234765'))

    await waitFor(() => expect(onContactsLoaded).toHaveBeenCalled())
    expect(onContactsLoaded.mock.calls[0][0].headers).toEqual(['name', 'phone'])
  })

  it('highlights while dragging over', async () => {
    const { container } = render(<CsvUpload onContactsLoaded={vi.fn()} />)
    const zone = container.firstChild

    // Asserted against the raw inline style: jsdom keeps CSS custom
    // properties as literal strings and does not resolve var(--accent).
    fireEvent.dragOver(zone, { dataTransfer: { files: [], types: ['Files'] } })
    await waitFor(() => expect(zone.getAttribute('style')).toContain('var(--accent)'))

    fireEvent.dragLeave(zone, { dataTransfer: { files: [], types: ['Files'] } })
    await waitFor(() => expect(zone.getAttribute('style')).toContain('var(--border)'))
  })

  it('ignores a drop with no file', async () => {
    const onContactsLoaded = vi.fn()
    const { container } = render(<CsvUpload onContactsLoaded={onContactsLoaded} />)

    fireEvent.drop(container.firstChild, { dataTransfer: { files: [] } })

    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(onContactsLoaded).not.toHaveBeenCalled()
  })
})
