import { useCallback, useEffect, useState } from 'react'
import { getWhatsappNumber, updateWhatsappNumber } from '../api'
import { classifyPhone } from '../utils/phone'

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
    let cancelled = false

    getWhatsappNumber()
      .then((data) => {
        if (!cancelled) setNumber(data.whatsapp_number || '')
      })
      .catch(() => {
        // Non-fatal — worst case the person sees "not set" and can still try
        // to set it, which surfaces any real error then.
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [])

  function startEdit() {
    setDraft(number)
    setError('')
    setEditing(true)
  }

  /**
   * Saves the draft number.
   *
   * Returns false on failure so the caller (the Enter key handler) knows not
   * to close the editor. Without that the field snapped shut on every failed
   * save, so the user lost their input and had to retype it with no idea why.
   */
  const save = useCallback(async () => {
    const trimmed = draft.trim()

    if (!trimmed) {
      setError('Enter a phone number.')
      return false
    }

    const local = classifyPhone(trimmed)
    if (local.status !== 'valid') {
      setError(
        local.status === 'needs-country'
          ? 'Include your country code, e.g. +2348161234765.'
          : 'That does not look like a valid phone number.'
      )
      return false
    }

    setError('')
    setSaving(true)
    try {
      const data = await updateWhatsappNumber(trimmed)
      setNumber(data.whatsapp_number)
      setEditing(false)
      return true
    } catch (err) {
      setError(err.message)
      return false
    } finally {
      setSaving(false)
    }
  }, [draft])

  async function handleSave() {
    await save()
  }

  function handleKeyDown(event) {
    if (event.key === 'Enter') {
      event.preventDefault()
      save()
    } else if (event.key === 'Escape') {
      setEditing(false)
    }
  }

  if (loading) return null

  if (editing) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
        <input
          className="bs-input"
          aria-label="WhatsApp phone number"
          style={{ width: '190px', padding: '6px 8px', fontSize: '13px' }}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="+(your country code) 1234567890"
          disabled={saving}
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
