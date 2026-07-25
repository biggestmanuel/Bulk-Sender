import { useState, useEffect, Fragment } from 'react'
import { isValidPhone } from '../utils/phone'

function ContactList({ headers, dataRows, mapping, onContactsUpdated }) {
  const nameIndex = headers.indexOf(mapping.nameCol)
  const phoneIndex = headers.indexOf(mapping.phoneCol)

  const [contacts, setContacts] = useState(dataRows)
  // Index of the row currently expanded for editing, or null if none.
  const [editingIndex, setEditingIndex] = useState(null)
  // Draft values for the row being edited — kept separate from `contacts`
  // so live validation can render against them without committing anything
  // until the person saves.
  const [draftName, setDraftName] = useState('')
  const [draftPhone, setDraftPhone] = useState('')

  useEffect(() => {
    setContacts(dataRows)
    setEditingIndex(null)
  }, [dataRows])

  function handleRemove(indexToRemove) {
    const updated = contacts.filter((_, i) => i !== indexToRemove)
    setContacts(updated)
    onContactsUpdated(updated)
    if (editingIndex === indexToRemove) setEditingIndex(null)
  }

  function handleStartEdit(index, row) {
    setEditingIndex(index)
    setDraftName(row[nameIndex])
    setDraftPhone(row[phoneIndex])
  }

  function handleCancelEdit() {
    setEditingIndex(null)
  }

  function handleSaveEdit(index) {
    const updated = contacts.map((row, i) => {
      if (i !== index) return row
      const newRow = [...row]
      newRow[nameIndex] = draftName
      newRow[phoneIndex] = draftPhone
      return newRow
    })
    setContacts(updated)
    onContactsUpdated(updated)
    setEditingIndex(null)
  }

  const validCount = contacts.filter(row => isValidPhone(row[phoneIndex])).length
  const invalidCount = contacts.length - validCount
  const draftValid = isValidPhone(draftPhone)

  return (
    <div className="bs-card" style={{ marginTop: 'var(--space-lg)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 'var(--space-md)' }}>
        <h2 style={{ margin: 0 }}>{contacts.length} contacts loaded</h2>
        <div style={{ display: 'flex', gap: '8px' }}>
          <span className="bs-badge bs-badge-success">{validCount} valid</span>
          {invalidCount > 0 && (
            <span className="bs-badge bs-badge-danger">{invalidCount} invalid</span>
          )}
        </div>
      </div>

      <div style={{ maxHeight: '420px', overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)' }}>
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
            {contacts.map((row, i) => {
              const valid = isValidPhone(row[phoneIndex])
              const isEditing = editingIndex === i

              return (
                <Fragment key={i}>
                  <tr style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '10px 12px' }}>{row[nameIndex]}</td>
                    <td style={{ padding: '10px 12px' }}>{row[phoneIndex]}</td>
                    <td style={{ padding: '10px 12px' }}>
                      <span className={valid ? 'bs-badge bs-badge-success' : 'bs-badge bs-badge-danger'}>
                        {valid ? 'Valid' : 'Invalid'}
                      </span>
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                      {!valid && !isEditing && (
                        <button
                          onClick={() => handleStartEdit(i, row)}
                          className="bs-btn bs-btn-secondary"
                          style={{ padding: '4px 10px', fontSize: '12px', marginRight: '6px' }}
                        >
                          Edit
                        </button>
                      )}
                      <button onClick={() => handleRemove(i)} className="bs-btn bs-btn-danger" style={{ padding: '4px 10px', fontSize: '12px' }}>
                        Remove
                      </button>
                    </td>
                  </tr>

                  {isEditing && (
                    <tr style={{ borderTop: '1px solid var(--border)', background: 'var(--bg-card-muted)' }}>
                      <td colSpan={4} style={{ padding: 'var(--space-md)' }}>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: 'var(--space-md)', alignItems: 'end' }}>
                          <div>
                            <label className="bs-label" htmlFor={`edit-name-${i}`}>Name</label>
                            <input
                              id={`edit-name-${i}`}
                              className="bs-input"
                              value={draftName}
                              onChange={(e) => setDraftName(e.target.value)}
                            />
                          </div>
                          <div>
                            <label className="bs-label" htmlFor={`edit-phone-${i}`}>Phone</label>
                            <input
                              id={`edit-phone-${i}`}
                              className="bs-input"
                              value={draftPhone}
                              onChange={(e) => setDraftPhone(e.target.value)}
                              style={{
                                borderColor: draftPhone ? (draftValid ? 'var(--accent)' : 'var(--danger)') : 'var(--border)',
                              }}
                            />
                            <p style={{
                              fontSize: '12px',
                              marginTop: '4px',
                              color: draftPhone ? (draftValid ? 'var(--accent)' : 'var(--danger)') : 'var(--text-muted)',
                            }}>
                              {draftPhone
                                ? (draftValid ? 'Looks good' : 'Still not a valid phone number')
                                : 'Enter a phone number'}
                            </p>
                          </div>
                          <div style={{ display: 'flex', gap: '8px', paddingBottom: '2px' }}>
                            <button
                              onClick={() => handleSaveEdit(i)}
                              disabled={!draftValid}
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