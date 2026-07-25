import { useState } from 'react'

function MessageComposer({ onMessageReady }) {
  const [message, setMessage] = useState('')
  const [mediaFiles, setMediaFiles] = useState([])
  const [error, setError] = useState('')

  function handleMediaChange(e) {
    const files = Array.from(e.target.files)
    setMediaFiles(prev => [...prev, ...files])
  }

  function handleRemoveFile(index) {
    setMediaFiles(prev => prev.filter((_, i) => i !== index))
  }

  function handleConfirm() {
    if (!message.trim()) {
      setError('Write a message before continuing.')
      return
    }
    setError('')
    onMessageReady({ message, mediaFiles })
  }

  return (
    <div className="bs-card" style={{ marginTop: 'var(--space-lg)' }}>
      <h2>Compose your message</h2>
      <p style={{ marginBottom: 'var(--space-md)' }}>
        Use <code style={{ background: 'var(--bg-card-muted)', padding: '1px 6px', borderRadius: '4px', fontSize: '13px' }}>{'{name}'}</code> anywhere to insert each contact's name.
      </p>

      <textarea
        rows="5"
        className="bs-input"
        placeholder="Hi {name}, just checking in about..."
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        style={{ resize: 'vertical', fontFamily: 'var(--font-body)', marginBottom: 'var(--space-md)' }}
      />

      <div style={{ marginBottom: 'var(--space-md)' }}>
        <label className="bs-label">Attachments (optional)</label>
        <label className="bs-btn bs-btn-secondary" style={{ cursor: 'pointer' }}>
          Add photos or videos
          <input type="file" accept="image/*,video/*" multiple onChange={handleMediaChange} style={{ display: 'none' }} />
        </label>
      </div>

      {mediaFiles.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 var(--space-md)', display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {mediaFiles.map((file, i) => (
            <li key={i} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: '13px', background: 'var(--bg-card-muted)', padding: '8px 12px', borderRadius: 'var(--radius-sm)' }}>
              <span>{file.name}</span>
              <button onClick={() => handleRemoveFile(i)} className="bs-btn bs-btn-danger" style={{ padding: '2px 8px', fontSize: '12px' }}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && (
        <p style={{ color: 'var(--danger)', fontSize: '13px', marginBottom: 'var(--space-md)' }}>
          {error}
        </p>
      )}

      <button onClick={handleConfirm} className="bs-btn bs-btn-primary">
        Confirm message
      </button>
    </div>
  )
}

export default MessageComposer
