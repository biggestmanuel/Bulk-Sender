import { useState } from 'react'
import Papa from 'papaparse'

// Uses Papaparse instead of a hand-rolled split('\n') / split(',') —
// the old version broke on any name/address containing a comma, a quoted
// field, or Windows-style \r\n line endings. Papaparse handles all of that.
function CsvUpload({ onContactsLoaded }) {
  const [fileName, setFileName] = useState('')
  const [error, setError] = useState('')
  const [isDragging, setIsDragging] = useState(false)

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

        onContactsLoaded({ headers, dataRows })
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

      {fileName && !error && (
        <p style={{ marginTop: 'var(--space-md)', fontSize: '13px', color: 'var(--text-primary)' }}>
          Loaded <strong>{fileName}</strong>
        </p>
      )}
      {error && (
        <p style={{ marginTop: 'var(--space-md)', color: 'var(--danger)', fontSize: '13px' }}>
          {error}
        </p>
      )}
    </div>
  )
}

export default CsvUpload
