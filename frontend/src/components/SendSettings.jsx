import { useState } from 'react'

function SendSettings({ onSettingsReady }) {
  const [mode, setMode] = useState('delay')

  function handleConfirm() {
    onSettingsReady({ mode })
  }

  return (
    <div className="bs-card" style={{ marginTop: 'var(--space-lg)' }}>
      <h2>Send settings</h2>
      <p style={{ marginBottom: 'var(--space-md)' }}>
        Choose how quickly messages go out.
      </p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: 'var(--space-md)' }}>
        <label
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: '10px',
            padding: '12px 14px',
            border: `1px solid ${mode === 'delay' ? 'var(--accent)' : 'var(--border)'}`,
            borderRadius: 'var(--radius-md)',
            background: mode === 'delay' ? 'var(--accent-bg)' : 'var(--bg-card)',
            cursor: 'pointer',
          }}
        >
          <input
            type="radio"
            name="sendMode"
            value="delay"
            checked={mode === 'delay'}
            onChange={() => setMode('delay')}
            style={{ marginTop: '3px' }}
          />
          <span>
            <strong style={{ display: 'block', fontSize: '14px' }}>Send with delay</strong>
            <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
              Randomized gaps between messages, with occasional longer pauses — mimics a human pace. Safer for your number, slower to finish.
            </span>
          </span>
        </label>

        <label
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: '10px',
            padding: '12px 14px',
            border: `1px solid ${mode === 'instant' ? 'var(--danger)' : 'var(--border)'}`,
            borderRadius: 'var(--radius-md)',
            background: mode === 'instant' ? 'var(--danger-bg)' : 'var(--bg-card)',
            cursor: 'pointer',
          }}
        >
          <input
            type="radio"
            name="sendMode"
            value="instant"
            checked={mode === 'instant'}
            onChange={() => setMode('instant')}
            style={{ marginTop: '3px' }}
          />
          <span>
            <strong style={{ display: 'block', fontSize: '14px' }}>Send all at once</strong>
            <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
              Fastest option, but carries a real risk of your WhatsApp number getting banned.
            </span>
          </span>
        </label>
      </div>

      <button onClick={handleConfirm} className="bs-btn bs-btn-primary">
        Confirm settings
      </button>
    </div>
  )
}

export default SendSettings