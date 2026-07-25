import { useState, useEffect, Fragment, useMemo } from 'react'
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

  useEffect(() => {
    setContacts(dataRows)
    setEditingIndex(null)
    setPickingCountryFor(null)
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

  function handleRemove(originalIndex) {
    const updated = contacts.filter((_, i) => i !== originalIndex)
    commitContacts(updated)
    if (editingIndex === originalIndex) setEditingIndex(null)
    if (pickingCountryFor === originalIndex) setPickingCountryFor(null)
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

      <div style={{ maxHeight: '460px', overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
          <thead>
            <tr style={{ background: 'var(--bg-card-muted)', position: 'sticky', top: 0 }}>
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
                  <tr style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '10px 12px' }}>{row[nameIndex]}</td>
                    <td style={{ padding: '10px 12px' }}>{row[phoneIndex]}</td>
                    <td style={{ padding: '10px 12px' }}>
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
                      <td colSpan={4} style={{ padding: 'var(--space-md)' }}>
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
                      <td colSpan={4} style={{ padding: 'var(--space-md)' }}>
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