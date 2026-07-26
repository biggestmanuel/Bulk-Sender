import { useState, useRef } from 'react'
import CsvUpload from './components/CsvUpload'
import ColumnMapping from './components/ColumnMapping'
import ContactList from './components/ContactList'
import MessageComposer from './components/MessageComposer'
import SendSettings from './components/SendSettings'
import SendProgress from './components/SendProgress'
import LoginCode from './components/LoginCode'
import WhatsAppNumberSettings from './components/WhatsAppNumberSettings'
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

  // When a second CSV is uploaded with different headers than the first,
  // we need to run column mapping again JUST for the incoming file before
  // we can convert its rows into the existing header shape and append them.
  // This holds that incoming file + mode while we wait for the person to
  // map its columns.
  const [pendingMerge, setPendingMerge] = useState(null) // { headers, dataRows } | null

  // Tracks whether we're still waiting on the WhatsApp login code to be
  // entered on the person's phone before the actual sending/progress
  // polling should start.
  const [awaitingLogin, setAwaitingLogin] = useState(false)
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

  function resetDownstreamState() {
    setMessageData(null)
    setSettings(null)
    setSentCount(0)
    setFailedCount(0)
    setError('')
    setEditingMapping(false)
    setEditingMessage(false)
    setEditingSettings(false)
  }

  function handleContactsLoaded(parsed, { mode }) {
    if (mode === 'replace' || !csvData) {
      setCsvData(parsed)
      setCurrentContacts(parsed.dataRows)
      setMapping(null)
      resetDownstreamState()
      return
    }

    // mode === 'merge'
    const headersMatch =
      parsed.headers.length === csvData.headers.length &&
      parsed.headers.every((h, i) => h === csvData.headers[i])

    if (headersMatch) {
      // Same column shape — just append directly, existing mapping still applies.
      setCurrentContacts((prev) => [...(prev || []), ...parsed.dataRows])
      resetDownstreamState()
    } else {
      // Different columns — hold onto the new file and ask the person to
      // map ITS columns before we can convert its rows into the existing shape.
      setPendingMerge(parsed)
    }
  }

  function handleMergeMappingDone(mergeMapping) {
    if (!pendingMerge || !csvData || !mapping) return

    const newNameIdx = pendingMerge.headers.indexOf(mergeMapping.nameCol)
    const newPhoneIdx = pendingMerge.headers.indexOf(mergeMapping.phoneCol)
    const existingNameIdx = csvData.headers.indexOf(mapping.nameCol)
    const existingPhoneIdx = csvData.headers.indexOf(mapping.phoneCol)

    // Build new rows shaped to match the EXISTING header layout, so the
    // current mapping (which points at existing header positions) still
    // works for every row, old and newly merged alike.
    const maxIndex = Math.max(existingNameIdx, existingPhoneIdx)
    const converted = pendingMerge.dataRows.map((row) => {
      const newRow = new Array(maxIndex + 1).fill('')
      newRow[existingNameIdx] = row[newNameIdx] ?? ''
      newRow[existingPhoneIdx] = row[newPhoneIdx] ?? ''
      return newRow
    })

    setCurrentContacts((prev) => [...(prev || []), ...converted])
    setPendingMerge(null)
    resetDownstreamState()
  }

  function handleCancelMerge() {
    setPendingMerge(null)
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

      // This throws with the backend's actual error message (e.g. "Add
      // your WhatsApp phone number in settings before sending") rather
      // than a generic failure, so the person knows exactly what to fix.
      await startSending(campaign.id)

      // Instead of polling progress immediately, wait for the login code
      // to be entered first. LoginCode will call handleLoginReady() once
      // WhatsApp is logged in, or handleLoginFailed() if it gives up.
      pendingCampaignIdRef.current = campaign.id
      setAwaitingLogin(true)

    } catch (err) {
      console.error(err)
      setError(err.message || 'Something went wrong starting the send. Check that the backend server is running.')
      setSending(false)
    }
  }

  function handleLoginReady() {
    setAwaitingLogin(false)
    if (pendingCampaignIdRef.current) {
      startProgressPolling(pendingCampaignIdRef.current)
    }
  }

  function handleLoginFailed() {
    setAwaitingLogin(false)
    setSending(false)
    setError('WhatsApp login timed out or failed. Please try again.')
  }

  // --- GATE: show login/register screen if not authenticated ---
  if (!authToken) {
    return <Auth onAuthSuccess={handleAuthSuccess} />
  }

  return (
    <div style={{ maxWidth: '760px', margin: '0 auto', padding: 'var(--space-xl) var(--space-lg)' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 'var(--space-lg)', gap: 'var(--space-md)', flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ marginBottom: '2px' }}>WhatsApp Bulk Sender</h1>
          <p style={{ fontSize: '13px' }}>Send at your own pace.</p>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 'var(--space-sm)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-md)' }}>
            <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>{username}</span>
            <button onClick={handleLogout} className="bs-btn bs-btn-secondary" style={{ padding: '6px 14px', fontSize: '13px' }}>
              Log out
            </button>
          </div>
          <WhatsAppNumberSettings />
        </div>
      </header>

      <CsvUpload onContactsLoaded={handleContactsLoaded} hasExistingContacts={!!currentContacts && currentContacts.length > 0} />

      {pendingMerge && (
        <div className="bs-card" style={{ marginTop: 'var(--space-lg)' }}>
          <h2>Map the new file's columns</h2>
          <p style={{ marginBottom: 'var(--space-md)' }}>
            This file's columns don't match your existing contacts. Tell us which column holds the name and phone number so we can merge it in.
          </p>
          <ColumnMapping headers={pendingMerge.headers} onMappingDone={handleMergeMappingDone} />
          <button onClick={handleCancelMerge} className="bs-btn bs-btn-secondary" style={{ marginTop: 'var(--space-sm)' }}>
            Cancel merge
          </button>
        </div>
      )}

      {csvData && (!mapping || editingMapping) && !pendingMerge && (
        <ColumnMapping headers={csvData.headers} onMappingDone={handleMappingDone} />
      )}

      {mapping && !editingMapping && !pendingMerge && (
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

      {awaitingLogin && <LoginCode onReady={handleLoginReady} onFailed={handleLoginFailed} />}

      {(sending || sentCount > 0 || failedCount > 0) && (
        <SendProgress total={currentContacts.length} sent={sentCount} failed={failedCount} />
      )}
    </div>
  )
}

export default App