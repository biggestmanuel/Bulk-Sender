import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ContactList from './ContactList'

const HEADERS = ['name', 'phone']

function rows(...pairs) {
  return pairs.map(([name, phone]) => [name, phone])
}

function renderList(dataRows, mapping = { nameCol: 'name', phoneCol: 'phone' }) {
  const onContactsUpdated = vi.fn()
  const utils = render(
    <ContactList
      headers={HEADERS}
      dataRows={dataRows}
      mapping={mapping}
      onContactsUpdated={onContactsUpdated}
    />
  )
  return { ...utils, onContactsUpdated }
}

describe('ContactList validation', () => {
  it('counts valid, needs-country and invalid rows', () => {
    renderList(
      rows(
        ['Valid', '+2348161234765'],
        ['NoCountry', '08012345678'],
        ['Broken', '+2341']
      )
    )

    expect(screen.getByText('1 valid')).toBeInTheDocument()
    expect(screen.getByText('1 need country')).toBeInTheDocument()
    expect(screen.getByText('1 invalid')).toBeInTheDocument()
  })

  it('sorts rows needing attention to the top', () => {
    renderList(
      rows(
        ['Valid one', '+2348161234765'],
        ['Broken', '+2341'],
        ['Valid two', '+2348161234766']
      )
    )

    const badges = screen.getAllByText(/^(Valid|Invalid|Needs country)$/)
    expect(badges[0]).toHaveTextContent('Invalid')
  })

  it('offers a country picker for rows that need one', async () => {
    const user = userEvent.setup()
    renderList(rows(['NoCountry', '08012345678']))

    await user.click(screen.getByRole('button', { name: 'Pick country' }))
    expect(screen.getByPlaceholderText('Search for a country…')).toBeInTheDocument()
  })

  it('shows an inline editor for invalid rows', async () => {
    const user = userEvent.setup()
    renderList(rows(['Broken', '+2341']))

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(screen.getByLabelText('Phone')).toBeInTheDocument()
  })
})

describe('ContactList editing', () => {
  it('commits a corrected phone number to the parent', async () => {
    const user = userEvent.setup()
    const { onContactsUpdated } = renderList(rows(['Broken', '+2341']))

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const input = screen.getByLabelText('Phone')
    await user.clear(input)
    await user.type(input, '+2348161234765')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(onContactsUpdated).toHaveBeenCalledWith([['Broken', '+2348161234765']])
  })

  it('discards changes on cancel', async () => {
    const user = userEvent.setup()
    const { onContactsUpdated } = renderList(rows(['Broken', '+2341']))

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.clear(screen.getByLabelText('Phone'))
    await user.type(screen.getByLabelText('Phone'), '+2348161234765')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(onContactsUpdated).not.toHaveBeenCalled()
  })

  it('gives live feedback while typing', async () => {
    const user = userEvent.setup()
    renderList(rows(['Broken', '+2341']))

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const input = screen.getByLabelText('Phone')
    await user.clear(input)
    await user.type(input, '+23481')
    expect(screen.getByText('Still not a valid phone number')).toBeInTheDocument()
  })
})

describe('ContactList removal and undo', () => {
  it('removes a row and reports the new list', async () => {
    const user = userEvent.setup()
    const { onContactsUpdated } = renderList(
      rows(['A', '+2348161234765'], ['B', '+2348161234766'])
    )

    await user.click(screen.getAllByRole('button', { name: 'Remove' })[0])

    expect(onContactsUpdated).toHaveBeenCalledWith([['B', '+2348161234766']])
  })

  it('offers undo after a removal', async () => {
    const user = userEvent.setup()
    renderList(rows(['A', '+2348161234765'], ['B', '+2348161234766']))

    await user.click(screen.getAllByRole('button', { name: 'Remove' })[0])

    expect(screen.getByText('Removed 1 contact')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument()
  })

  it('restores a removed row at its original position', async () => {
    const user = userEvent.setup()
    const { onContactsUpdated } = renderList(
      rows(['A', '+2348161234765'], ['B', '+2348161234766'], ['C', '+2348161234767'])
    )

    // Remove the middle row, then undo.
    await user.click(screen.getAllByRole('button', { name: 'Remove' })[1])
    await user.click(screen.getByRole('button', { name: 'Undo' }))

    expect(onContactsUpdated).toHaveBeenLastCalledWith([
      ['A', '+2348161234765'],
      ['B', '+2348161234766'],
      ['C', '+2348161234767'],
    ])
  })

  it('confirms before a bulk removal', async () => {
    const user = userEvent.setup()
    const { onContactsUpdated } = renderList(
      rows(['A', '+2348161234765'], ['B', '+2348161234766'])
    )

    await user.click(screen.getByRole('checkbox', { name: 'Select all contacts' }))
    await user.click(screen.getByRole('button', { name: 'Remove selected' }))

    // Not removed yet — an explicit confirmation is required.
    expect(onContactsUpdated).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Yes, remove' }))
    expect(onContactsUpdated).toHaveBeenCalledWith([])
  })

  it('abandons a bulk removal on cancel', async () => {
    const user = userEvent.setup()
    const { onContactsUpdated } = renderList(
      rows(['A', '+2348161234765'], ['B', '+2348161234766'])
    )

    await user.click(screen.getByRole('checkbox', { name: 'Select all contacts' }))
    await user.click(screen.getByRole('button', { name: 'Remove selected' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(onContactsUpdated).not.toHaveBeenCalled()
  })

  /**
   * The original text said "This can't be undone" directly above an Undo
   * button. Users who trusted the copy had no reason to look for it.
   */
  it('does not claim removal is permanent when undo is offered', async () => {
    const user = userEvent.setup()
    renderList(rows(['A', '+2348161234765'], ['B', '+2348161234766']))

    await user.click(screen.getByRole('checkbox', { name: 'Select all contacts' }))
    await user.click(screen.getByRole('button', { name: 'Remove selected' }))

    expect(screen.queryByText(/can't be undone/i)).not.toBeInTheDocument()
  })

  it('bulk-removes only the selected rows', async () => {
    const user = userEvent.setup()
    const { onContactsUpdated } = renderList(
      rows(
        ['A', '+2348161234765'],
        ['B', '+2348161234766'],
        ['C', '+2348161234767']
      )
    )

    await user.click(screen.getByRole('checkbox', { name: 'Select all contacts' }))
    await user.click(screen.getByRole('button', { name: 'Done' })).catch(() => {})

    // Re-enter selection mode and remove everything, then undo.
    await user.click(screen.getByRole('checkbox', { name: 'Select all contacts' }))
    await user.click(screen.getByRole('button', { name: 'Remove selected' }))
    await user.click(screen.getByRole('button', { name: 'Yes, remove' }))
    expect(onContactsUpdated).toHaveBeenCalledWith([])

    await user.click(screen.getByRole('button', { name: 'Undo' }))
    expect(onContactsUpdated).toHaveBeenLastCalledWith([
      ['A', '+2348161234765'],
      ['B', '+2348161234766'],
      ['C', '+2348161234767'],
    ])
  })
})

describe('ContactList prop sync', () => {
  /**
   * When the parent echoes an edit back down as a prop, the component used to
   * reset all of its own state — which made the undo banner vanish the moment
   * it appeared, and dropped the user out of selection mode.
   */
  it('keeps undo available when the parent echoes the change back', async () => {
    const user = userEvent.setup()
    const dataRows = rows(['A', '+2348161234765'], ['B', '+2348161234766'])
    const onContactsUpdated = vi.fn()

    const { rerender } = render(
      <ContactList headers={HEADERS} dataRows={dataRows} mapping={{ nameCol: 'name', phoneCol: 'phone' }} onContactsUpdated={onContactsUpdated} />
    )

    await user.click(screen.getAllByRole('button', { name: 'Remove' })[0])
    const emitted = onContactsUpdated.mock.calls[0][0]

    rerender(
      <ContactList headers={HEADERS} dataRows={emitted} mapping={{ nameCol: 'name', phoneCol: 'phone' }} onContactsUpdated={onContactsUpdated} />
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Undo' }))
    expect(onContactsUpdated).toHaveBeenLastCalledWith(dataRows)
  })
})