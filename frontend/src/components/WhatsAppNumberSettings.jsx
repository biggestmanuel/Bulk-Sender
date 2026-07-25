import { useState, useEffect } from 'react'
import { getWhatsappNumber, updateWhatsappNumber } from '../api'

// Shown in the app header. Lets the person set — or change, anytime — the
// WhatsApp number their account links with during a send. Required before
// start_sending will succeed (enforced server-side); this just gives them
// a place to set/edit it without digging through a full settings page.
function WhatsAppNumberSettings() {
  const [number, setNumber] = useState('')
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    getWhatsappNumber()
      .then((data) => setNumber(data.whatsapp_number || ''))
      .catch(() => {
        // Non-fatal — worst case the person just sees "not set" and can
        // still try to set it, which will surface any real error then.
      })
      .finally(() => setLoading(false))
  }, [])

  function startEdit() {
    setDraft(number)
    setError('')
    setEditing(true)
  }

  async function handleSave() {
    setError('')
    setSaving(true)
    try {
      const data = await updateWhatsappNumber(draft.trim())
      setNumber(data.whatsapp_number)
      setEditing(false)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return null

  if (editing) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
        <input
          className="bs-input"
          style={{ width: '170px', padding: '6px 8px', fontSize: '13px' }}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="+(your country code) 1234567890"
          autoFocus
        />
        <button onClick={handleSave} disabled={saving} className="bs-btn bs-btn-primary" style={{ padding: '6px 12px', fontSize: '12px' }}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button onClick={() => setEditing(false)} className="bs-btn bs-btn-secondary" style={{ padding: '6px 12px', fontSize: '12px' }}>
          Cancel
        </button>
        {error && (
          <span style={{ color: 'var(--danger)', fontSize: '12px', width: '100%', textAlign: 'right' }}>
            {error}
          </span>
        )}
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', color: 'var(--text-secondary)' }}>
      <span>
        WhatsApp number:{' '}
        {number
          ? <strong style={{ color: 'var(--text-primary)' }}>{number}</strong>
          : <em style={{ color: 'var(--danger)' }}>not set</em>}
      </span>
      <button onClick={startEdit} className="bs-btn bs-btn-secondary" style={{ padding: '4px 10px', fontSize: '12px' }}>
        {number ? 'Change' : 'Add'}
      </button>
    </div>
  )
}

export default WhatsAppNumberSettings
