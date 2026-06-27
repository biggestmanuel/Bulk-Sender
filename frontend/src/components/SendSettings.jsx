import { useState } from 'react'

function SendSettings({ onSettingsReady }) {
  const [mode, setMode] = useState('delay')

  function handleConfirm() {
    onSettingsReady({ mode })
  }

  return (
    <div style={{ marginTop: '20px', padding: '20px', border: '1px solid lightgray' }}>
      <h3>Send Settings</h3>

      <label style={{ display: 'block', marginBottom: '8px' }}>
        <input
          type="radio"
          name="sendMode"
          value="delay"
          checked={mode === 'delay'}
          onChange={() => setMode('delay')}
        />
        {' '}Send with delay (15-30 sec between each) — safer, slower
      </label>

      <label style={{ display: 'block', marginBottom: '8px' }}>
        <input
          type="radio"
          name="sendMode"
          value="instant"
          checked={mode === 'instant'}
          onChange={() => setMode('instant')}
        />
        {' '}Send all at once — fast, higher risk
      </label>

      <button onClick={handleConfirm} style={{ marginTop: '10px' }}>
        Confirm Settings
      </button>
    </div>
  )
}

export default SendSettings