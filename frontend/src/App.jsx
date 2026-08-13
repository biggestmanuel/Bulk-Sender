import { useEffect, useMemo, useState } from 'react'
import { createCampaign, getCampaignStatus, logoutUser, startSending } from './api'
import Auth from './components/Auth'
import ColumnMapping from './components/ColumnMapping'
import ContactList from './components/ContactList'
import CsvUpload from './components/CsvUpload'
import LoginCode from './components/LoginCode'
import MessageComposer from './components/MessageComposer'
import SendProgress from './components/SendProgress'
import SendSettings from './components/SendSettings'
import WhatsAppNumberSettings from './components/WhatsAppNumberSettings'

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(Boolean(localStorage.getItem('authToken')))
  const [username, setUsername] = useState(localStorage.getItem('username') || '')
  const [headers, setHeaders] = useState([])
  const [contacts, setContacts] = useState([])
  const [mapping, setMapping] = useState(null)
  const [messageData, setMessageData] = useState(null)
  const [sendMode, setSendMode] = useState('delay')
  const [campaignId, setCampaignId] = useState(null)
  const [campaignStatus, setCampaignStatus] = useState(null)
  const [error, setError] = useState('')
  const [isBusy, setIsBusy] = useState(false)
  const [loginOverlay, setLoginOverlay] = useState(false)

  useEffect(() => {
    if (!localStorage.getItem('authToken')) {
      setIsAuthenticated(false)
    }
  }, [])

  useEffect(() => {
    if (!campaignId) return

    const poll = async () => {
      try {
        const status = await getCampaignStatus(campaignId)
        setCampaignStatus(status)
      } catch (err) {
        console.error('Campaign status poll failed:', err)
      }
    }

    poll()
    const timer = setInterval(poll, 3000)
    return () => clearInterval(timer)
  }, [campaignId])

  function handleAuthSuccess(token, nextUsername) {
    localStorage.setItem('authToken', token)
    localStorage.setItem('username', nextUsername)
    setUsername(nextUsername)
    setIsAuthenticated(true)
  }

  function clearDraftState() {
    setHeaders([])
    setContacts([])
    setMapping(null)
    setMessageData(null)
    setSendMode('delay')
    setCampaignId(null)
    setCampaignStatus(null)
    setError('')
    setIsBusy(false)
    setLoginOverlay(false)
  }

  function handleContactsLoaded(parsed, options = { mode: 'replace' }) {
    const mergedRows = options.mode === 'merge' && contacts.length > 0
      ? [...contacts, ...parsed.dataRows]
      : parsed.dataRows

    setHeaders(parsed.headers)
    setContacts(mergedRows)
    setMapping(null)
    setCampaignId(null)
    setCampaignStatus(null)
    setError('')

    if (parsed.headers.includes('name') && parsed.headers.includes('phone')) {
      setMapping({ nameCol: 'name', phoneCol: 'phone' })
    }
  }

  function handleLogout() {
    logoutUser()
    setIsAuthenticated(false)
    setUsername('')
    clearDraftState()
  }

  const sendSummary = useMemo(() => {
    const total = campaignStatus?.contacts?.length || 0
    const sent = campaignStatus?.contacts?.filter((contact) => contact.status === 'sent').length || 0
    const failed = campaignStatus?.contacts?.filter((contact) => contact.status === 'failed').length || 0
    return { total, sent, failed }
  }, [campaignStatus])

  async function handleStartCampaign() {
    if (!headers.length || !mapping) {
      setError('Map the CSV columns before starting the campaign.')
      return
    }

    if (!messageData || !messageData.message.trim()) {
      setError('Write a message before sending.')
      return
    }

    if (!contacts.length) {
      setError('Upload at least one contact.')
      return
    }

    setIsBusy(true)
    setError('')

    try {
      const payloadContacts = contacts.map((row) => ({
        name: row[mapping.nameCol] ?? '',
        phone: row[mapping.phoneCol] ?? '',
      }))

      const created = await createCampaign({
        name: `Campaign ${new Date().toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}`,
        messageText: messageData.message,
        sendMode,
        contacts: payloadContacts,
        mediaFiles: messageData.mediaFiles || [],
      })

      setCampaignId(created.id)
      setLoginOverlay(true)
      await startSending(created.id)
    } catch (err) {
      setError(err.message || 'Something went wrong while starting the campaign.')
    } finally {
      setIsBusy(false)
    }
  }

  if (!isAuthenticated) {
    return <Auth onAuthSuccess={handleAuthSuccess} />
  }

  return (
    <div style={{ maxWidth: '1080px', margin: '0 auto', padding: 'var(--space-xl) var(--space-lg)' }}>
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 'var(--space-md)',
          flexWrap: 'wrap',
          marginBottom: 'var(--space-lg)',
        }}
      >
        <div>
          <p style={{ textTransform: 'uppercase', letterSpacing: '0.12em', fontSize: '12px', color: 'var(--text-secondary)' }}>
            Bulk sender
          </p>
          <h1 style={{ margin: 0 }}>Campaign workspace</h1>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
          <WhatsAppNumberSettings />
          <span className="bs-badge bs-badge-muted">{username || 'User'}</span>
          <button onClick={handleLogout} className="bs-btn bs-btn-secondary" style={{ padding: '8px 12px', fontSize: '13px' }}>
            Log out
          </button>
        </div>
      </header>

      {error && (
        <div className="bs-card" style={{ marginBottom: 'var(--space-lg)', borderColor: 'var(--danger)' }}>
          <p style={{ color: 'var(--danger)', fontWeight: 500 }}>{error}</p>
        </div>
      )}

      <CsvUpload onContactsLoaded={handleContactsLoaded} hasExistingContacts={contacts.length > 0} />

      {contacts.length > 0 && !mapping && (
        <ColumnMapping headers={headers} onMappingDone={setMapping} />
      )}

      {contacts.length > 0 && mapping && (
        <>
          <ContactList
            headers={headers}
            dataRows={contacts}
            mapping={mapping}
            onContactsUpdated={setContacts}
          />

          <MessageComposer onMessageReady={({ message, mediaFiles }) => setMessageData({ message, mediaFiles })} />

          <SendSettings onSettingsReady={({ mode }) => setSendMode(mode)} />

          {messageData && (
            <div style={{ marginTop: 'var(--space-lg)', display: 'flex', justifyContent: 'flex-end' }}>
              <button
                className="bs-btn bs-btn-primary"
                disabled={isBusy}
                onClick={handleStartCampaign}
                style={{ minWidth: '180px' }}
              >
                {isBusy ? 'Starting…' : 'Start sending'}
              </button>
            </div>
          )}
        </>
      )}

      {campaignId && sendSummary.total > 0 && (
        <SendProgress
          total={sendSummary.total}
          sent={sendSummary.sent}
          failed={sendSummary.failed}
        />
      )}

      {loginOverlay && (
        <LoginCode
          onReady={() => setLoginOverlay(false)}
          onFailed={() => {
            setLoginOverlay(false)
            setError('WhatsApp login failed. Please try the campaign again.')
          }}
        />
      )}
    </div>
  )
}

export default App


