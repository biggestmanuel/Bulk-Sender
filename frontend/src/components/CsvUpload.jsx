import { useState } from 'react'
import Papa from 'papaparse'

// Uses Papaparse instead of a hand-rolled split('\n') / split(',') —
// the old version broke on any name/address containing a comma, a quoted
// field, or Windows-style \r\n line endings. Papaparse handles all of that.
//
// hasExistingContacts tells us whether to ask merge-vs-replace before
// handing the parsed file back up to App.jsx. The actual merge/replace
// decision and header-reconciliation logic lives in App.jsx, since that's
// where the existing contact list state actually lives — this component
// only needs to know *whether* to ask, not how to combine anything.
function CsvUpload({ onContactsLoaded, hasExistingContacts }) {
  const [fileName, setFileName] = useState('')
  const [error, setError] = useState('')
  const [isDragging, setIsDragging] = useState(false)
  const [pendingParsedFile, setPendingParsedFile] = useState(null)

  function parseFile(file) {
    if (!file) return

    setFileName(file.name)
    setError('')

    Papa.parse(file, {
      skipEmptyLines: true,
      complete: (results) => {
        const rows = results.data

        if (!rows || rows.length < 2) {
          setError('This CSV looks empty or is missing a header row.')
          return
        }

        const headers = rows[0].map((h) => (h ?? '').trim())
        const dataRows = rows
          .slice(1)
          .map((row) => row.map((cell) => (cell ?? '').trim()))

        const parsed = { headers, dataRows }

        if (hasExistingContacts) {
          // Don't hand this off immediately — ask merge vs replace first.
          setPendingParsedFile(parsed)
        } else {
          onContactsLoaded(parsed, { mode: 'replace' })
        }
      },
      error: (err) => {
        setError(`Couldn't read that CSV: ${err.message}`)
      },
    })
  }

  function handleFileInput(e) {
    parseFile(e.target.files[0])
  }

  function handleDrop(e) {
    e.preventDefault()
    setIsDragging(false)
    parseFile(e.dataTransfer.files[0])
  }

  function handleMergeChoice(mode) {
    if (pendingParsedFile) {
      onContactsLoaded(pendingParsedFile, { mode })
    }
    setPendingParsedFile(null)
  }

  return (
    <div
      className="bs-card"
      onDragOver={(e) => { e.preventDefault(); setIsDragging(true) }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={handleDrop}
      style={{
        textAlign: 'center',
        borderStyle: 'dashed',
        borderColor: isDragging ? 'var(--accent)' : 'var(--border)',
        background: isDragging ? 'var(--accent-bg)' : 'var(--bg-card)',
        transition: 'border-color 0.15s ease, background 0.15s ease',
      }}
    >
      <h2>Upload contacts</h2>
      <p style={{ marginBottom: 'var(--space-md)' }}>
        Drop a CSV here, or choose a file. It needs a header row with a name column and a phone column.
      </p>
      <label className="bs-btn bs-btn-secondary" style={{ cursor: 'pointer' }}>
        Choose CSV file
        <input type="file" accept=".csv" onChange={handleFileInput} style={{ display: 'none' }} />
      </label>

      {fileName && !error && !pendingParsedFile && (
        <p style={{ marginTop: 'var(--space-md)', fontSize: '13px', color: 'var(--text-primary)' }}>
          Loaded <strong>{fileName}</strong>
        </p>
      )}
      {error && (
        <p style={{ marginTop: 'var(--space-md)', color: 'var(--danger)', fontSize: '13px' }}>
          {error}
        </p>
      )}

      {pendingParsedFile && (
        <div
          className="bs-card"
          style={{ marginTop: 'var(--space-md)', textAlign: 'left', background: 'var(--bg-card-muted)', borderStyle: 'solid' }}
        >
          <p style={{ marginBottom: 'var(--space-md)', color: 'var(--text-primary)', fontSize: '13px' }}>
            You already have contacts loaded. Add <strong>{fileName}</strong> to the existing list, or replace it entirely?
          </p>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button onClick={() => handleMergeChoice('merge')} className="bs-btn bs-btn-primary" style={{ padding: '8px 16px', fontSize: '13px' }}>
              Merge with existing
            </button>
            <button onClick={() => handleMergeChoice('replace')} className="bs-btn bs-btn-danger" style={{ padding: '8px 16px', fontSize: '13px' }}>
              Replace existing
            </button>
            <button onClick={() => setPendingParsedFile(null)} className="bs-btn bs-btn-secondary" style={{ padding: '8px 16px', fontSize: '13px' }}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default CsvUpload