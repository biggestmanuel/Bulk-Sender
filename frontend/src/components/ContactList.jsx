import { useState, useEffect, useRef, Fragment, useMemo } from 'react'
import { classifyPhone, resolveWithCountry } from '../utils/phone'
import CountryPicker from './CountryPicker'

// Sort order: rows needing attention (needs-country, then invalid) always
// float to the top, so a person scanning a long list immediately sees what
// needs fixing instead of hunting for it. Valid rows follow, in their
// original order.
const STATUS_RANK = { 'needs-country': 0, invalid: 1, valid: 2 }

function ContactList({ headers, dataRows, mapping, onContactsUpdated }) {
  const nameIndex = headers.indexOf(mapping.nameCol)
  const phoneIndex = headers.indexOf(mapping.phoneCol)

  const [contacts, setContacts] = useState(dataRows)
  const [editingIndex, setEditingIndex] = useState(null)
  const [draftName, setDraftName] = useState('')
  const [draftPhone, setDraftPhone] = useState('')
  // Which row (by original index) currently has its country picker open —
  // separate from editingIndex since "needs a country" and "editing the
  // name/phone text" are different interactions.
  const [pickingCountryFor, setPickingCountryFor] = useState(null)

  // Multi-select state. Keyed by originalIndex (same identity used
  // everywhere else in this component), so selection survives re-sorting
  // and stays correctly attached to a row even if the sort order shifts
  // underneath it (e.g. a selected row gets edited and its status changes).
  //
  // Row checkboxes only render once selection mode is switched on via the
  // header checkbox — keeps the default table clean (no checkbox column
  // eating into Name/Phone/Status alignment) until the person actually
  // wants to bulk-remove something.
  const [selectionModeActive, setSelectionModeActive] = useState(false)
  const [selected, setSelected] = useState(() => new Set())
  const [confirmingBulkRemove, setConfirmingBulkRemove] = useState(false)

  // Undo state, shared by single-row and bulk remove. Removed rows aren't
  // deleted from `contacts` right away — they're held here for 5 seconds
  // with their original position, so "Undo" can splice them back in
  // exactly where they were. If the timer runs out, or another remove
  // happens first, the pending batch is just abandoned (already gone
  // from `contacts`, nothing left to do).
  //   pendingUndo: { rows: [{ row, originalIndex }], count } | null
  const [pendingUndo, setPendingUndo] = useState(null)
  const undoTimerRef = useRef(null)

  useEffect(() => {
    return () => {
      if (undoTimerRef.current) clearTimeout(undoTimerRef.current)
    }
  }, [])

  useEffect(() => {
    setContacts(dataRows)
    setEditingIndex(null)
    setPickingCountryFor(null)
    setSelectionModeActive(false)
    setSelected(new Set())
    setConfirmingBulkRemove(false)
    if (undoTimerRef.current) clearTimeout(undoTimerRef.current)
    setPendingUndo(null)
  }, [dataRows])

  // Classify every row once per render, keeping the original index attached
  // so edits/removals still target the right underlying row after sorting.
  const classified = useMemo(() => {
    return contacts.map((row, originalIndex) => ({
      row,
      originalIndex,
      classification: classifyPhone(row[phoneIndex]),
    }))
  }, [contacts, phoneIndex])

  const sorted = useMemo(() => {
    return [...classified].sort((a, b) => {
      const rankDiff = STATUS_RANK[a.classification.status] - STATUS_RANK[b.classification.status]
      if (rankDiff !== 0) return rankDiff
      return a.originalIndex - b.originalIndex
    })
  }, [classified])

  function commitContacts(updated) {
    setContacts(updated)
    onContactsUpdated(updated)
  }

  // Removes a single row, then holds it for 5s so it can be undone.
  function handleRemove(originalIndex) {
    const removedRow = contacts[originalIndex]
    const updated = contacts.filter((_, i) => i !== originalIndex)

    commitContacts(updated)
    if (editingIndex === originalIndex) setEditingIndex(null)
    if (pickingCountryFor === originalIndex) setPickingCountryFor(null)
    setSelected((prev) => {
      if (!prev.has(originalIndex)) return prev
      const next = new Set(prev)
      next.delete(originalIndex)
      return next
    })

    startUndoWindow([{ row: removedRow, originalIndex }])
  }

  // Shared by single-row and bulk remove. `removedEntries` is the list of
  // { row, originalIndex } pairs just taken out of `contacts`, in their
  // original ascending index order — that order matters for undo, since
  // re-inserting them one at a time by ascending index puts everything
  // back exactly where it was, regardless of how many rows were removed
  // at once.
  function startUndoWindow(removedEntries) {
    if (undoTimerRef.current) clearTimeout(undoTimerRef.current)

    setPendingUndo({ rows: removedEntries, count: removedEntries.length })

    undoTimerRef.current = setTimeout(() => {
      setPendingUndo(null)
      undoTimerRef.current = null
    }, 5000)
  }

  function handleUndoRemove() {
    if (!pendingUndo) return
    if (undoTimerRef.current) {
      clearTimeout(undoTimerRef.current)
      undoTimerRef.current = null
    }

    // Re-insert removed rows back at their original indices. Since
    // `contacts` has shrunk since removal, indices are restored by
    // inserting in ascending order — each insertion shifts everything
    // after it by one, which is exactly what's needed to line the next
    // one up correctly too.
    let restored = [...contacts]
    for (const { row, originalIndex } of pendingUndo.rows) {
      const insertAt = Math.min(originalIndex, restored.length)
      restored = [...restored.slice(0, insertAt), row, ...restored.slice(insertAt)]
    }

    commitContacts(restored)
    setPendingUndo(null)
  }

  function handleStartEdit(originalIndex, row) {
    setEditingIndex(originalIndex)
    setPickingCountryFor(null)
    setDraftName(row[nameIndex])
    setDraftPhone(row[phoneIndex])
  }

  function handleCancelEdit() {
    setEditingIndex(null)
  }

  function handleSaveEdit(originalIndex) {
    const updated = contacts.map((row, i) => {
      if (i !== originalIndex) return row
      const newRow = [...row]
      newRow[nameIndex] = draftName
      newRow[phoneIndex] = draftPhone
      return newRow
    })
    commitContacts(updated)
    setEditingIndex(null)
  }

  function handleCountrySelected(originalIndex, country) {
    const row = contacts[originalIndex]
    const result = resolveWithCountry(row[phoneIndex], country.code)

    if (result.status === 'valid') {
      const updated = contacts.map((r, i) => {
        if (i !== originalIndex) return r
        const newRow = [...r]
        newRow[phoneIndex] = result.e164
        return newRow
      })
      commitContacts(updated)
    }
    // If it still doesn't resolve to valid, leave the row as-is — it'll
    // fall through to "invalid" on the next render, which at least gives
    // clear feedback rather than silently discarding their country choice.
    setPickingCountryFor(null)
  }

  // --- Multi-select handlers ---

  function toggleRowSelected(originalIndex) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(originalIndex)) {
        next.delete(originalIndex)
      } else {
        next.add(originalIndex)
      }
      return next
    })
  }

  const allSelected = contacts.length > 0 && selected.size === contacts.length
  const someSelected = selected.size > 0 && !allSelected

  // The header checkbox does double duty: checking it turns on selection
  // mode (revealing per-row checkboxes) AND selects everything, in one
  // click — the common case is "I want to bulk-remove most of these."
  // Unchecking it exits selection mode entirely, hiding the row checkboxes
  // again rather than leaving an empty checkbox column behind.
  function toggleSelectAll() {
    if (selectionModeActive) {
      setSelectionModeActive(false)
      setSelected(new Set())
    } else {
      setSelectionModeActive(true)
      setSelected(new Set(contacts.map((_, i) => i)))
    }
  }

  function handleBulkRemoveClick() {
    setConfirmingBulkRemove(true)
  }

  function handleConfirmBulkRemove() {
    const removedEntries = contacts
      .map((row, i) => ({ row, originalIndex: i }))
      .filter(({ originalIndex }) => selected.has(originalIndex))
    const updated = contacts.filter((_, i) => !selected.has(i))

    commitContacts(updated)
    if (editingIndex !== null && selected.has(editingIndex)) setEditingIndex(null)
    if (pickingCountryFor !== null && selected.has(pickingCountryFor)) setPickingCountryFor(null)
    setSelected(new Set())
    setSelectionModeActive(false)
    setConfirmingBulkRemove(false)

    startUndoWindow(removedEntries)
  }

  function handleCancelBulkRemove() {
    setConfirmingBulkRemove(false)
  }

  const validCount = classified.filter((c) => c.classification.status === 'valid').length
  const needsCountryCount = classified.filter((c) => c.classification.status === 'needs-country').length
  const invalidCount = classified.filter((c) => c.classification.status === 'invalid').length
  const draftClassification = classifyPhone(draftPhone)
  const draftValid = draftClassification.status === 'valid'

  return (
    <div className="bs-card" style={{ marginTop: 'var(--space-lg)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 'var(--space-md)', flexWrap: 'wrap', gap: '8px' }}>
        <h2 style={{ margin: 0 }}>{contacts.length} contacts loaded</h2>
        <div style={{ display: 'flex', gap: '8px' }}>
          <span className="bs-badge bs-badge-success">{validCount} valid</span>
          {needsCountryCount > 0 && (
            <span className="bs-badge bs-badge-muted">{needsCountryCount} need country</span>
          )}
          {invalidCount > 0 && (
            <span className="bs-badge bs-badge-danger">{invalidCount} invalid</span>
          )}
        </div>
      </div>

      {/* Undo banner — shown for 5s after any remove (single or bulk).
          Placed above the bulk action bar since the two can't really
          overlap in practice (removing clears selection first). */}
      {pendingUndo && (
        <div
          className="bs-card"
          style={{
            marginBottom: 'var(--space-md)',
            background: 'var(--bg-card-muted)',
            borderStyle: 'solid',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: '8px',
            padding: 'var(--space-md)',
          }}
        >
          <span style={{ fontSize: '13px', color: 'var(--text-primary)' }}>
            Removed {pendingUndo.count} contact{pendingUndo.count === 1 ? '' : 's'}
          </span>
          <button onClick={handleUndoRemove} className="bs-btn bs-btn-secondary" style={{ padding: '6px 14px', fontSize: '13px' }}>
            Undo
          </button>
        </div>
      )}

      {/* Bulk action bar — shown whenever selection mode is on, so the
          person always has a visible way to exit it, whether or not
          anything is currently checked. */}
      {selectionModeActive && (
        <div
          className="bs-card"
          style={{
            marginBottom: 'var(--space-md)',
            background: 'var(--bg-card-muted)',
            borderStyle: 'solid',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: '8px',
            padding: 'var(--space-md)',
          }}
        >
          {!confirmingBulkRemove ? (
            <>
              <span style={{ fontSize: '13px', color: 'var(--text-primary)' }}>
                {selected.size} selected
              </span>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  onClick={handleBulkRemoveClick}
                  disabled={selected.size === 0}
                  className="bs-btn bs-btn-danger"
                  style={{ padding: '6px 14px', fontSize: '13px' }}
                >
                  Remove selected
                </button>
                <button
                  onClick={() => { setSelectionModeActive(false); setSelected(new Set()) }}
                  className="bs-btn bs-btn-secondary"
                  style={{ padding: '6px 14px', fontSize: '13px' }}
                >
                  Done
                </button>
              </div>
            </>
          ) : (
            <>
              <span style={{ fontSize: '13px', color: 'var(--danger)' }}>
                Remove {selected.size} contact{selected.size === 1 ? '' : 's'}? This can't be undone.
              </span>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button onClick={handleConfirmBulkRemove} className="bs-btn bs-btn-danger" style={{ padding: '6px 14px', fontSize: '13px' }}>
                  Yes, remove
                </button>
                <button onClick={handleCancelBulkRemove} className="bs-btn bs-btn-secondary" style={{ padding: '6px 14px', fontSize: '13px' }}>
                  Cancel
                </button>
              </div>
            </>
          )}
        </div>
      )}

      <div style={{ maxHeight: '460px', overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
          <thead>
            <tr style={{ background: 'var(--bg-card-muted)', position: 'sticky', top: 0 }}>
              <th style={{ padding: '10px 12px', width: '1%', textAlign: 'center' }}>
                <input
                  type="checkbox"
                  checked={selectionModeActive}
                  ref={(el) => { if (el) el.indeterminate = selectionModeActive && someSelected }}
                  onChange={toggleSelectAll}
                  aria-label={selectionModeActive ? 'Exit selection mode' : 'Select all contacts'}
                />
              </th>
              <th style={{ textAlign: 'left', padding: '10px 12px', color: 'var(--text-secondary)', fontWeight: 500 }}>Name</th>
              <th style={{ textAlign: 'left', padding: '10px 12px', color: 'var(--text-secondary)', fontWeight: 500 }}>Phone</th>
              <th style={{ textAlign: 'left', padding: '10px 12px', color: 'var(--text-secondary)', fontWeight: 500 }}>Status</th>
              <th style={{ padding: '10px 12px' }}></th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(({ row, originalIndex, classification }) => {
              const { status } = classification
              const isEditing = editingIndex === originalIndex
              const isPickingCountry = pickingCountryFor === originalIndex
              const isSelected = selected.has(originalIndex)

              const badgeClass =
                status === 'valid' ? 'bs-badge bs-badge-success' :
                status === 'needs-country' ? 'bs-badge bs-badge-muted' :
                'bs-badge bs-badge-danger'
              const badgeLabel =
                status === 'valid' ? 'Valid' :
                status === 'needs-country' ? 'Needs country' :
                'Invalid'

              return (
                <Fragment key={originalIndex}>
                  <tr
                    style={{
                      borderTop: '1px solid var(--border)',
                      background: isSelected ? 'var(--accent-bg)' : undefined,
                    }}
                  >
                    <td style={{ padding: '10px 12px', textAlign: 'center' }}>
                      {selectionModeActive && (
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleRowSelected(originalIndex)}
                          aria-label={`Select ${row[nameIndex] || 'contact'}`}
                        />
                      )}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'left' }}>{row[nameIndex]}</td>
                    <td style={{ padding: '10px 12px', textAlign: 'left' }}>{row[phoneIndex]}</td>
                    <td style={{ padding: '10px 12px', textAlign: 'left' }}>
                      <span className={badgeClass}>{badgeLabel}</span>
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                      {status === 'needs-country' && !isPickingCountry && (
                        <button
                          onClick={() => { setPickingCountryFor(originalIndex); setEditingIndex(null) }}
                          className="bs-btn bs-btn-secondary"
                          style={{ padding: '4px 10px', fontSize: '12px', marginRight: '6px' }}
                        >
                          Pick country
                        </button>
                      )}
                      {status === 'invalid' && !isEditing && (
                        <button
                          onClick={() => handleStartEdit(originalIndex, row)}
                          className="bs-btn bs-btn-secondary"
                          style={{ padding: '4px 10px', fontSize: '12px', marginRight: '6px' }}
                        >
                          Edit
                        </button>
                      )}
                      <button onClick={() => handleRemove(originalIndex)} className="bs-btn bs-btn-danger" style={{ padding: '4px 10px', fontSize: '12px' }}>
                        Remove
                      </button>
                    </td>
                  </tr>

                  {isPickingCountry && (
                    <tr style={{ borderTop: '1px solid var(--border)', background: 'var(--bg-card-muted)' }}>
                      <td colSpan={5} style={{ padding: 'var(--space-md)' }}>
                        <p style={{ fontSize: '13px', marginBottom: 'var(--space-sm)', color: 'var(--text-primary)' }}>
                          "{row[phoneIndex]}" has no country code — which country is this number from?
                        </p>
                        <div style={{ maxWidth: '320px' }}>
                          <CountryPicker
                            onSelect={(country) => handleCountrySelected(originalIndex, country)}
                            onCancel={() => setPickingCountryFor(null)}
                          />
                        </div>
                      </td>
                    </tr>
                  )}

                  {isEditing && (
                    <tr style={{ borderTop: '1px solid var(--border)', background: 'var(--bg-card-muted)' }}>
                      <td colSpan={5} style={{ padding: 'var(--space-md)' }}>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: 'var(--space-md)', alignItems: 'end' }}>
                          <div>
                            <label className="bs-label" htmlFor={`edit-name-${originalIndex}`}>Name</label>
                            <input
                              id={`edit-name-${originalIndex}`}
                              className="bs-input"
                              value={draftName}
                              onChange={(e) => setDraftName(e.target.value)}
                            />
                          </div>
                          <div>
                            <label className="bs-label" htmlFor={`edit-phone-${originalIndex}`}>Phone</label>
                            <input
                              id={`edit-phone-${originalIndex}`}
                              className="bs-input"
                              value={draftPhone}
                              onChange={(e) => setDraftPhone(e.target.value)}
                              placeholder="+2348161234765"
                              style={{
                                borderColor: draftPhone ? (draftValid ? 'var(--accent)' : 'var(--danger)') : 'var(--border)',
                              }}
                            />
                            <p style={{
                              fontSize: '12px',
                              marginTop: '4px',
                              color: draftPhone ? (draftValid ? 'var(--accent)' : 'var(--danger)') : 'var(--text-muted)',
                            }}>
                              {!draftPhone
                                ? 'Enter a phone number'
                                : draftClassification.status === 'valid'
                                  ? 'Looks good'
                                  : draftClassification.status === 'needs-country'
                                    ? 'Add a + and country code, or save and pick a country from the list'
                                    : 'Still not a valid phone number'}
                            </p>
                          </div>
                          <div style={{ display: 'flex', gap: '8px', paddingBottom: '2px' }}>
                            <button
                              onClick={() => handleSaveEdit(originalIndex)}
                              className="bs-btn bs-btn-primary"
                              style={{ padding: '10px 16px', fontSize: '13px' }}
                            >
                              Save
                            </button>
                            <button
                              onClick={handleCancelEdit}
                              className="bs-btn bs-btn-secondary"
                              style={{ padding: '10px 16px', fontSize: '13px' }}
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default ContactList