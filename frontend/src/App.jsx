import { useState, useRef } from 'react'
import CsvUpload from './components/CsvUpload'
import ColumnMapping from './components/ColumnMapping'
import ContactList from './components/ContactList'
import MessageComposer from './components/MessageComposer'
import SendSettings from './components/SendSettings'
import SendProgress from './components/SendProgress'
import QrScanner from './components/QrScanner'
import Auth from './components/Auth'
import { isValidPhone } from './utils/phone'
import { createCampaign, startSending, getCampaignStatus, logoutUser } from './api'

function App() {
  // --- AUTH STATE ---
  const [authToken, setAuthToken] = useState(localStorage.getItem('authToken'))
  const [username, setUsername] = useState(localStorage.getItem('username'))

  const [csvData, setCsvData] = useState(null)
  const [currentContacts, setCurrentContacts] = useState(null)
  const [mapping, setMapping] = useState(null)
  const [messageData, setMessageData] = useState(null)
  const [settings, setSettings] = useState(null)
  const [sending, setSending] = useState(false)
  const [sentCount, setSentCount] = useState(0)
  const [failedCount, setFailedCount] = useState(0)
  const [error, setError] = useState('')

  const [editingMapping, setEditingMapping] = useState(false)
  const [editingMessage, setEditingMessage] = useState(false)
  const [editingSettings, setEditingSettings] = useState(false)

  // Tracks whether we're still waiting on a WhatsApp QR scan before
  // the actual sending/progress polling should start.
  const [awaitingQrScan, setAwaitingQrScan] = useState(false)
  const pendingCampaignIdRef = useRef(null)

  const pollIntervalRef = useRef(null)

  function handleAuthSuccess(token, user) {
    setAuthToken(token)
    setUsername(user)
  }

  function handleLogout() {
    logoutUser()
    setAuthToken(null)
    setUsername(null)
  }

  function handleContactsLoaded(data) {
    setCsvData(data)
    setCurrentContacts(data.dataRows)
    setMapping(null)
    setMessageData(null)
    setSettings(null)
    setSentCount(0)
    setFailedCount(0)
    setError('')
    setEditingMapping(false)
    setEditingMessage(false)
    setEditingSettings(false)
  }

  function handleMappingDone(mappingResult) {
    setMapping(mappingResult)
    setEditingMapping(false)
  }

  function handleContactsUpdated(updatedContacts) {
    setCurrentContacts(updatedContacts)
  }

  function handleMessageReady(data) {
    setMessageData(data)
    setEditingMessage(false)
  }

  function handleSettingsReady(data) {
    setSettings(data)
    setEditingSettings(false)
  }

  function startProgressPolling(campaignId) {
    pollIntervalRef.current = setInterval(async () => {
      try {
        const status = await getCampaignStatus(campaignId)
        const sent = status.contacts.filter(c => c.status === 'sent').length
        const failed = status.contacts.filter(c => c.status === 'failed').length
        setSentCount(sent)
        setFailedCount(failed)

        if (sent + failed >= status.contacts.length) {
          clearInterval(pollIntervalRef.current)
          setSending(false)
        }
      } catch (err) {
        console.error('Polling error:', err)
      }
    }, 3000)
  }

  async function handleStartSend() {
    const nameIndex = csvData.headers.indexOf(mapping.nameCol)
    const phoneIndex = csvData.headers.indexOf(mapping.phoneCol)

    // Refuse to submit while any contact still has an invalid phone number,
    // instead of quietly sending a smaller campaign than the UI showed.
    // The backend re-validates too, but the user should decide what to do
    // with invalid rows (fix or remove them) rather than have them vanish.
    const invalidContacts = currentContacts.filter(row => !isValidPhone(row[phoneIndex]))
    if (invalidContacts.length > 0) {
      setError(
        `${invalidContacts.length} contact(s) have an invalid phone number. ` +
        `Remove or fix them in the contact list above before sending.`
      )
      return
    }

    if (settings.mode === 'instant') {
      const confirmed = window.confirm(
        'You chose "Send all at once". This carries a real risk of the WhatsApp number getting banned. Continue?'
      )
      if (!confirmed) return
    }

    setError('')
    setSending(true)
    setSentCount(0)
    setFailedCount(0)

    try {
      const contactsPayload = currentContacts.map(row => ({
        name: row[nameIndex],
        phone: row[phoneIndex],
      }))

      const campaign = await createCampaign({
        name: `Campaign ${new Date().toLocaleString()}`,
        messageText: messageData.message,
        sendMode: settings.mode,
        contacts: contactsPayload,
        mediaFiles: messageData.mediaFiles || [],
      })

      await startSending(campaign.id)

      // Instead of polling progress immediately, wait for QR login first.
      // QrScanner will call handleQrReady() once WhatsApp is logged in.
      pendingCampaignIdRef.current = campaign.id
      setAwaitingQrScan(true)

    } catch (err) {
      console.error(err)
      setError('Something went wrong starting the send. Check that the backend server is running.')
      setSending(false)
    }
  }

  function handleQrReady() {
    setAwaitingQrScan(false)
    if (pendingCampaignIdRef.current) {
      startProgressPolling(pendingCampaignIdRef.current)
    }
  }

  // --- GATE: show login/register screen if not authenticated ---
  if (!authToken) {
    return <Auth onAuthSuccess={handleAuthSuccess} />
  }

  return (
    <div style={{ maxWidth: '760px', margin: '0 auto', padding: 'var(--space-xl) var(--space-lg)' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-lg)' }}>
        <div>
          <h1 style={{ marginBottom: '2px' }}>WhatsApp Bulk Sender</h1>
          <p style={{ fontSize: '13px' }}>Send at your own pace.</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-md)' }}>
          <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>{username}</span>
          <button onClick={handleLogout} className="bs-btn bs-btn-secondary" style={{ padding: '6px 14px', fontSize: '13px' }}>
            Log out
          </button>
        </div>
      </header>

      <CsvUpload onContactsLoaded={handleContactsLoaded} />

      {csvData && (!mapping || editingMapping) && (
        <ColumnMapping headers={csvData.headers} onMappingDone={handleMappingDone} />
      )}

      {mapping && !editingMapping && (
        <div>
          <ContactList
            headers={csvData.headers}
            dataRows={currentContacts}
            mapping={mapping}
            onContactsUpdated={handleContactsUpdated}
          />
          <button onClick={() => setEditingMapping(true)} className="bs-btn bs-btn-secondary" style={{ marginTop: 'var(--space-sm)' }}>
            Edit column mapping
          </button>
        </div>
      )}

      {mapping && (!messageData || editingMessage) && (
        <MessageComposer onMessageReady={handleMessageReady} />
      )}

      {messageData && !editingMessage && (
        <div className="bs-card" style={{ marginTop: 'var(--space-lg)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <p style={{ fontSize: '13px', color: 'var(--text-primary)' }}>
            Message ready: "{messageData.message.slice(0, 60)}{messageData.message.length > 60 ? '…' : ''}"
          </p>
          <button
            onClick={() => { setEditingMessage(true); setSentCount(0); setFailedCount(0); }}
            className="bs-btn bs-btn-secondary"
            style={{ padding: '6px 14px', fontSize: '13px' }}
          >
            Edit message
          </button>
        </div>
      )}

      {messageData && (!settings || editingSettings) && (
        <SendSettings onSettingsReady={handleSettingsReady} />
      )}

      {settings && !editingSettings && (
        <div className="bs-card" style={{ marginTop: 'var(--space-lg)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <p style={{ fontSize: '13px', color: 'var(--text-primary)' }}>
            Send mode: {settings.mode === 'delay' ? 'With delay (safer)' : 'All at once (risky)'}
          </p>
          <button
            onClick={() => { setEditingSettings(true); setSentCount(0); setFailedCount(0); }}
            className="bs-btn bs-btn-secondary"
            style={{ padding: '6px 14px', fontSize: '13px' }}
          >
            Edit settings
          </button>
        </div>
      )}

      {messageData && settings && !sending && sentCount === 0 && failedCount === 0 && !editingMessage && !editingMapping && !editingSettings && (
        <button onClick={handleStartSend} className="bs-btn bs-btn-primary" style={{ marginTop: 'var(--space-lg)', padding: '12px 24px' }}>
          Start send
        </button>
      )}

      {error && (
        <p style={{ color: 'var(--danger)', marginTop: 'var(--space-md)', fontSize: '13px' }}>{error}</p>
      )}

      {awaitingQrScan && <QrScanner onReady={handleQrReady} />}

      {(sending || sentCount > 0 || failedCount > 0) && (
        <SendProgress total={currentContacts.length} sent={sentCount} failed={failedCount} />
      )}
    </div>
  )
}

export default App
