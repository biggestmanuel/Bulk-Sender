import { useState } from 'react'

function ColumnMapping({ headers, onMappingDone }) {
  const [nameCol, setNameCol] = useState('')
  const [phoneCol, setPhoneCol] = useState('')

  function handleConfirm() {
    if (!nameCol || !phoneCol) {
      alert('Please select both a Name column and a Phone column')
      return
    }
    onMappingDone({ nameCol, phoneCol })
  }

  return (
    <div style={{ marginTop: '20px', padding: '20px', border: '1px solid lightgray' }}>
      <h3>Map your columns</h3>

      <div style={{ marginBottom: '10px' }}>
        <label>Which column is the Name? </label>
        <select value={nameCol} onChange={(e) => setNameCol(e.target.value)}>
          <option value="">-- Select --</option>
          {headers.map((header, i) => (
            <option key={i} value={header}>{header}</option>
          ))}
        </select>
      </div>

      <div style={{ marginBottom: '10px' }}>
        <label>Which column is the Phone number? </label>
        <select value={phoneCol} onChange={(e) => setPhoneCol(e.target.value)}>
          <option value="">-- Select --</option>
          {headers.map((header, i) => (
            <option key={i} value={header}>{header}</option>
          ))}
        </select>
      </div>

      <button onClick={handleConfirm}>Confirm Mapping</button>
    </div>
  )
}

export default ColumnMapping