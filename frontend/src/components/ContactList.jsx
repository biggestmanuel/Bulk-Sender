import { useState, useEffect } from 'react'

function isValidPhone(phone) {
  const cleaned = phone.replace(/[\s\-()]/g, '')
  const regex = /^\+?[0-9]{10,15}$/
  return regex.test(cleaned)
}

function ContactList({ headers, dataRows, mapping, onContactsUpdated }) {
  const nameIndex = headers.indexOf(mapping.nameCol)
  const phoneIndex = headers.indexOf(mapping.phoneCol)

  const [contacts, setContacts] = useState(dataRows)

  useEffect(() => {
    setContacts(dataRows)
  }, [dataRows])

  function handleRemove(indexToRemove) {
    const updated = contacts.filter((_, i) => i !== indexToRemove)
    setContacts(updated)
    onContactsUpdated(updated)
  }

  const validCount = contacts.filter(row => isValidPhone(row[phoneIndex])).length
  const invalidCount = contacts.length - validCount

  return (
    <div style={{ marginTop: '20px' }}>
      <h3>Contacts Loaded: {contacts.length}</h3>
      <p>
        ✅ Valid numbers: {validCount}
        {invalidCount > 0 && <span style={{ color: 'red' }}> | ⚠️ Invalid numbers: {invalidCount}</span>}
      </p>
      <table border="1" cellPadding="8">
        <thead>
          <tr>
            <th>Name</th>
            <th>Phone</th>
            <th>Status</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {contacts.map((row, i) => {
            const valid = isValidPhone(row[phoneIndex])
            return (
              <tr key={i} style={{ background: valid ? 'white' : '#ffe5e5' }}>
                <td>{row[nameIndex]}</td>
                <td>{row[phoneIndex]}</td>
                <td>{valid ? '✅ Valid' : '⚠️ Invalid'}</td>
                <td>
                  <button onClick={() => handleRemove(i)}>Remove</button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export default ContactList