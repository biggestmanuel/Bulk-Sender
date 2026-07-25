import { useState } from 'react'

function ColumnMapping({ headers, onMappingDone }) {
  const [nameCol, setNameCol] = useState('')
  const [phoneCol, setPhoneCol] = useState('')
  const [error, setError] = useState('')

  function handleConfirm() {
    if (!nameCol || !phoneCol) {
      setError('Choose both a name column and a phone column.')
      return
    }
    setError('')
    onMappingDone({ nameCol, phoneCol })
  }

  return (
    <div className="bs-card" style={{ marginTop: 'var(--space-lg)' }}>
      <h2>Map your columns</h2>
      <p style={{ marginBottom: 'var(--space-md)' }}>
        Tell us which column holds each contact's name and phone number.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-md)', marginBottom: 'var(--space-md)' }}>
        <div>
          <label className="bs-label" htmlFor="name-col">Name column</label>
          <select
            id="name-col"
            className="bs-input"
            value={nameCol}
            onChange={(e) => setNameCol(e.target.value)}
          >
            <option value="">Select a column</option>
            {headers.map((header, i) => (
              <option key={i} value={header}>{header}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="bs-label" htmlFor="phone-col">Phone column</label>
          <select
            id="phone-col"
            className="bs-input"
            value={phoneCol}
            onChange={(e) => setPhoneCol(e.target.value)}
          >
            <option value="">Select a column</option>
            {headers.map((header, i) => (
              <option key={i} value={header}>{header}</option>
            ))}
          </select>
        </div>
      </div>

      {error && (
        <p style={{ color: 'var(--danger)', fontSize: '13px', marginBottom: 'var(--space-md)' }}>
          {error}
        </p>
      )}

      <button onClick={handleConfirm} className="bs-btn bs-btn-primary">
        Confirm mapping
      </button>
    </div>
  )
}

export default ColumnMapping
