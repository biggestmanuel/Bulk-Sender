import { useState } from 'react'
import Papa from 'papaparse'

// Uses Papaparse instead of a hand-rolled split('\n') / split(',') —
// the old version broke on any name/address containing a comma, a quoted
// field, or Windows-style \r\n line endings. Papaparse handles all of that.
function CsvUpload({ onContactsLoaded }) {
  const [fileName, setFileName] = useState('')
  const [error, setError] = useState('')

  function handleFile(e) {
    const file = e.target.files[0]
    if (!file) return

    setFileName(file.name)
    setError('')

    Papa.parse(file, {
      skipEmptyLines: true,
      complete: (results) => {
        const rows = results.data

        if (!rows || rows.length < 2) {
          setError('CSV appears to be empty or missing a header row.')
          return
        }

        const headers = rows[0].map((h) => (h ?? '').trim())
        const dataRows = rows
          .slice(1)
          .map((row) => row.map((cell) => (cell ?? '').trim()))

        onContactsLoaded({ headers, dataRows })
      },
      error: (err) => {
        setError(`Could not read CSV: ${err.message}`)
      },
    })
  }

  return (
    <div style={{ border: '2px dashed gray', padding: '30px', textAlign: 'center' }}>
      <p>Drop CSV here or click to browse</p>
      <input type="file" accept=".csv" onChange={handleFile} />
      {fileName && !error && <p>Loaded: {fileName}</p>}
      {error && <p style={{ color: 'red' }}>{error}</p>}
    </div>
  )
}

export default CsvUpload
