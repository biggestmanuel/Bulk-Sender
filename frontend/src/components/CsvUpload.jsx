import { useState } from 'react'

function CsvUpload({ onContactsLoaded }) {
  const [fileName, setFileName] = useState('')

  function handleFile(e) {
    const file = e.target.files[0]
    if (!file) return

    setFileName(file.name)

    const reader = new FileReader()
    reader.onload = (event) => {
      const text = event.target.result
      const rows = text.split('\n').filter(row => row.trim() !== '')
      const headers = rows[0].split(',').map(h => h.trim())
      const dataRows = rows.slice(1).map(row => row.split(',').map(cell => cell.trim()))

      onContactsLoaded({ headers, dataRows })
    }
    reader.readAsText(file)
  }

  return (
    <div style={{ border: '2px dashed gray', padding: '30px', textAlign: 'center' }}>
      <p>Drop CSV here or click to browse</p>
      <input type="file" accept=".csv" onChange={handleFile} />
      {fileName && <p>Loaded: {fileName}</p>}
    </div>
  )
}

export default CsvUpload