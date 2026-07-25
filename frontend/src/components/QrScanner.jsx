import { useState, useEffect } from 'react'

// Shows a WhatsApp QR code image while the backend is waiting for a scan.
// Polls the backend every 2 seconds to check whether a QR is ready,
// shows a "slow network" warning if it's taking a long time,
// and calls onReady() once WhatsApp has logged in (QR disappears).
//
// NOTE: sends the auth token with every poll. QR state is per-user on
// the backend, so without the token the server wouldn't know whose QR to
// return — this is what makes it safe for two people to start a send at
// the same time without seeing or overwriting each other's QR code.
function QrScanner({ onReady }) {
  const [qrUrl, setQrUrl] = useState(null)
  const [everSawQr, setEverSawQr] = useState(false)
  const [slowNetwork, setSlowNetwork] = useState(false)

  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const token = localStorage.getItem('authToken')
        const res = await fetch('http://127.0.0.1:8000/api/qr-status/', {
          headers: token ? { Authorization: `Token ${token}` } : {},
        })
        const data = await res.json()

        if (data.qr_ready) {
          setEverSawQr(true)
          setSlowNetwork(!!data.qr_slow)
          // cache-buster so the browser always fetches the latest image
          setQrUrl(`http://127.0.0.1:8000${data.qr_url}?t=${Date.now()}`)
        } else if (everSawQr) {
          // QR existed before, now it's gone — WhatsApp must be logged in
          clearInterval(interval)
          setQrUrl(null)
          onReady()
        }
      } catch (err) {
        console.error('QR poll error:', err)
      }
    }, 2000)

    return () => clearInterval(interval)
  }, [everSawQr, onReady])

  if (!qrUrl) return null

  return (
    <div className="bs-card" style={{ marginTop: 'var(--space-lg)', textAlign: 'center' }}>
      <h2>Scan this code with WhatsApp</h2>
      <p style={{ marginBottom: 'var(--space-md)' }}>
        Open WhatsApp on your phone → Settings → Linked devices → Link a device.
      </p>
      <img
        src={qrUrl}
        alt="WhatsApp QR code"
        style={{ maxWidth: '260px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)' }}
      />

      {slowNetwork && (
        <p style={{ marginTop: 'var(--space-md)', color: 'var(--danger)', fontWeight: 500, fontSize: '13px' }}>
          This is taking a while — check your connection. Still waiting.
        </p>
      )}
    </div>
  )
}

export default QrScanner
