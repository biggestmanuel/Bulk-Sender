import { useState } from 'react'

function MessageComposer({ onMessageReady }) {
  const [message, setMessage] = useState('')
  const [mediaFiles, setMediaFiles] = useState([])

  function handleMediaChange(e) {
    const files = Array.from(e.target.files)
    setMediaFiles(prev => [...prev, ...files])
  }

  function handleRemoveFile(index) {
    setMediaFiles(prev => prev.filter((_, i) => i !== index))
  }

  function handleConfirm() {
    if (!message.trim()) {
      alert('Please type a message')
      return
    }
    onMessageReady({ message, mediaFiles })
  }

  return (
    <div style={{ marginTop: '20px', padding: '20px', border: '1px solid lightgray' }}>
      <h3>Compose your message</h3>

      <textarea
        rows="4"
        cols="50"
        placeholder="Type your message here... Use {name} to insert each contact's name automatically"
        value={message}
        onChange={(e) => setMessage(e.target.value)}
      />
      <p style={{ fontSize: '12px', color: 'gray' }}>
        Tip: Type <code>{'{name}'}</code> anywhere in your message and it'll be replaced with each contact's name.
      </p>

      <div style={{ marginTop: '10px' }}>
        <label>Attach images or videos (optional): </label>
        <input type="file" accept="image/*,video/*" multiple onChange={handleMediaChange} />
      </div>

      {mediaFiles.length > 0 && (
        <ul style={{ marginTop: '10px' }}>
          {mediaFiles.map((file, i) => (
            <li key={i}>
              {file.name}{' '}
              <button onClick={() => handleRemoveFile(i)} style={{ marginLeft: '8px' }}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      <button onClick={handleConfirm} style={{ marginTop: '10px' }}>
        Confirm Message
      </button>
    </div>
  )
}

export default MessageComposer