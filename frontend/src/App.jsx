import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  createCampaign,
  getCampaignStatus,
  logoutUser,
  setUnauthorizedHandler,
  startSending,
} from './api'
import Auth from './components/Auth'
import CampaignList from './components/CampaignList'
import ColumnMapping from './components/ColumnMapping'
import ContactList from './components/ContactList'
import CsvUpload from './components/CsvUpload'
import LoginCode from './components/LoginCode'
import MessageComposer from './components/MessageComposer'
import SendProgress from './components/SendProgress'
import SendSettings from './components/SendSettings'
import WhatsAppNumberSettings from './components/WhatsAppNumberSettings'
import { classifyPhone } from './utils/phone'

const POLL_INTERVAL_MS = 3000
// Internal heartbeat; the actual request only fires when the backoff gap has
// elapsed, so this only controls how promptly we notice that it has.
const POLL_HEARTBEAT_MS = 1000
// Ceiling for the backoff. A 30s cadence costs ~120 requests/hour for a
// long-running send, comfortably inside the API's polling budget.
const MAX_POLL_INTERVAL_MS = 30000

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(() =>
    Boolean(localStorage.getItem('authToken'))
  )
  const [username, setUsername] = useState(() => localStorage.getItem('username') || '')
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

  // A 401 anywhere means the token is dead. Handle it centrally instead of
  // letting every component discover it separately and render a broken screen.
  const handleExpired = useCallback(() => {
    setIsAuthenticated(false)
    setUsername('')
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(handleExpired)
    return () => setUnauthorizedHandler(null)
  }, [handleExpired])

  const clearDraftState = useCallback(() => {
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
  }, [])

  // --- Campaign status polling ---
  // Stops on its own once the campaign reaches a terminal state. The old
  // effect polled every 3 seconds for the lifetime of the page, which meant
  // a request per user per 3 seconds forever after any single send, and kept
  // decrypting every contact in the campaign on each poll.
  const isFinished = useMemo(() => {
    const status = campaignStatus?.campaign_status
    return status === 'completed' || status === 'failed'
  }, [campaignStatus])

  const isSending = useMemo(() => {
    const status = campaignStatus?.campaign_status
    return status === 'running' || status === 'queued'
  }, [campaignStatus])

  useEffect(() => {
    if (!campaignId || isFinished) return undefined

    let cancelled = false
    let gapMs = POLL_INTERVAL_MS
    let lastFetchAt = 0

    const poll = async () => {
      try {
        const status = await getCampaignStatus(campaignId)
        if (!cancelled) setCampaignStatus(status)
      } catch (err) {
        // A transient network blip shouldn't tear down the view; the next
        // tick retries. Only auth failures are handled globally.
        if (!cancelled) console.error('Campaign status poll failed:', err)
      }
    }

    const tick = async () => {
      // A fixed heartbeat decides whether enough time has passed to actually
      // ask the server. Delay mode on a 5,000-contact campaign runs for
      // hours; polling every 3 seconds throughout would be over 16,000
      // requests — wasteful, and enough to exhaust the API's polling budget
      // and freeze the progress panel on a send that was still running.
      // Early on the counters move fast enough to be worth the tight gap;
      // once the send is clearly underway, a longer one loses nothing
      // visible.
      const now = Date.now()
      if (now - lastFetchAt < gapMs) return

      lastFetchAt = now
      gapMs = Math.min(gapMs * 2, MAX_POLL_INTERVAL_MS)
      await poll()
    }

    // Immediate first poll, then a 1s heartbeat.
    lastFetchAt = Date.now()
    poll()
    const timer = setInterval(tick, POLL_HEARTBEAT_MS)

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [campaignId, isFinished])

  const handleAuthSuccess = useCallback((token, nextUsername) => {
    localStorage.setItem('authToken', token)
    localStorage.setItem('username', nextUsername)
    setUsername(nextUsername)
    setIsAuthenticated(true)
  }, [])

  const handleContactsLoaded = useCallback(
    (parsed, options = { mode: 'replace' }) => {
      const mergedRows =
        options.mode === 'merge' && contacts.length > 0
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
    },
    [contacts]
  )

  const handleLogout = useCallback(() => {
    logoutUser()
    setIsAuthenticated(false)
    setUsername('')
    clearDraftState()
  }, [clearDraftState])

  const handleLoginReady = useCallback(() => setLoginOverlay(false), [])

  const handleLoginFailed = useCallback(() => {
    setLoginOverlay(false)
    setError('WhatsApp login failed. Please try the campaign again.')
  }, [])

  /**
   * A retry started from the campaign list needs to show the login overlay and
   * progress panel, which live here rather than in CampaignList.
   */
  const handleCampaignRestarted = useCallback((restartedCampaignId) => {
    setCampaignId(restartedCampaignId)
    setCampaignStatus(null)
    setLoginOverlay(true)
  }, [])

  // --- Contact validation, mirrored from ContactList so Start can be gated ---
  const phoneIndex = mapping ? headers.indexOf(mapping.phoneCol) : -1
  const contactSummary = useMemo(() => {
    if (phoneIndex < 0) return { total: 0, valid: 0, invalid: 0, needsCountry: 0 }
    let valid = 0
    let invalid = 0
    let needsCountry = 0
    for (const row of contacts) {
      const { status } = classifyPhone(row[phoneIndex])
      if (status === 'valid') valid += 1
      else if (status === 'needs-country') needsCountry += 1
      else invalid += 1
    }
    return { total: contacts.length, valid, invalid, needsCountry }
  }, [contacts, phoneIndex])

  const sendSummary = useMemo(() => {
    // Prefer the server's grouped counts; it computes them in one query.
    if (campaignStatus?.progress) {
      const { total, sent, failed } = campaignStatus.progress
      return { total, sent, failed }
    }
    const list = campaignStatus?.contacts || []
    return {
      total: list.length,
      sent: list.filter((c) => c.status === 'sent').length,
      failed: list.filter((c) => c.status === 'failed').length,
    }
  }, [campaignStatus])

  const canStart =
    Boolean(headers.length) &&
    Boolean(mapping) &&
    Boolean(messageData?.message?.trim()) &&
    contacts.length > 0 &&
    contactSummary.invalid === 0 &&
    contactSummary.needsCountry === 0 &&
    contactSummary.valid > 0

  const startBlockedReason = useMemo(() => {
    if (!contacts.length) return 'Upload at least one contact.'
    if (!mapping) return 'Map the CSV columns before starting.'
    if (contactSummary.invalid > 0) {
      return `${contactSummary.invalid} contact${contactSummary.invalid === 1 ? '' : 's'} ` +
        `still ${contactSummary.invalid === 1 ? 'has' : 'have'} an invalid phone number. ` +
        'Edit or remove them first.'
    }
    if (contactSummary.needsCountry > 0) {
      return `${contactSummary.needsCountry} contact${
        contactSummary.needsCountry === 1 ? '' : 's'
      } still ${contactSummary.needsCountry === 1 ? 'needs' : 'need'} a country code. ` +
        'Pick a country for them first.'
    }
    if (!messageData?.message?.trim()) return 'Write a message before sending.'
    return null
  }, [contacts.length, mapping, contactSummary, messageData])

  const startCampaign = useCallback(async () => {
    if (startBlockedReason) {
      setError(startBlockedReason)
      return
    }

    setIsBusy(true)
    setError('')

    const nameIndex = headers.indexOf(mapping.nameCol)

    try {
      const created = await createCampaign({
        name: `Campaign ${new Date().toLocaleString([], {
          month: 'short',
          day: 'numeric',
          hour: 'numeric',
          minute: '2-digit',
        })}`,
        messageText: messageData.message,
        sendMode,
        contacts: contacts.map((row) => ({
          name: row[nameIndex] ?? '',
          phone: row[phoneIndex] ?? '',
        })),
        mediaFiles: messageData.mediaFiles || [],
      })

      setCampaignId(created.id)
      setLoginOverlay(true)
      await startSending(created.id)
    } catch (err) {
      // The backend rejects the whole campaign if any number is unusable and
      // names the offenders, so surface that instead of a generic failure.
      setError(err.message || 'Something went wrong while starting the campaign.')
    } finally {
      setIsBusy(false)
    }
  }, [startBlockedReason, headers, mapping, messageData, sendMode, contacts, phoneIndex])

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
          <p
            style={{
              textTransform: 'uppercase',
              letterSpacing: '0.12em',
              fontSize: '12px',
              color: 'var(--text-secondary)',
            }}
          >
            Bulk sender
          </p>
          <h1 style={{ margin: 0 }}>Campaign workspace</h1>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
          <WhatsAppNumberSettings />
          <span className="bs-badge bs-badge-muted">{username || 'User'}</span>
          <button
            onClick={handleLogout}
            className="bs-btn bs-btn-secondary"
            style={{ padding: '8px 12px', fontSize: '13px' }}
          >
            Log out
          </button>
        </div>
      </header>

      {error && (
        <div
          className="bs-card"
          style={{ marginBottom: 'var(--space-lg)', borderColor: 'var(--danger)' }}
        >
          <p style={{ color: 'var(--danger)', fontWeight: 500, margin: 0 }}>{error}</p>
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

          <MessageComposer onMessageReady={setMessageData} />

          <SendSettings onSettingsReady={({ mode }) => setSendMode(mode)} />

          {messageData && (
            <div style={{ marginTop: 'var(--space-lg)' }}>
              {startBlockedReason && (
                <p
                  style={{
                    fontSize: '13px',
                    color: 'var(--text-secondary)',
                    marginBottom: 'var(--space-sm)',
                    textAlign: 'right',
                  }}
                >
                  {startBlockedReason}
                </p>
              )}
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <button
                  className="bs-btn bs-btn-primary"
                  disabled={isBusy || !canStart || isSending}
                  onClick={startCampaign}
                  style={{ minWidth: '180px' }}
                >
                  {isBusy ? 'Starting…' : isSending ? 'Sending…' : 'Start sending'}
                </button>
              </div>
            </div>
          )}
        </>
      )}

      {campaignId && sendSummary.total > 0 && (
        <SendProgress
          total={sendSummary.total}
          sent={sendSummary.sent}
          failed={sendSummary.failed}
          campaignStatus={campaignStatus?.campaign_status}
          statusDetail={campaignStatus?.status_detail}
          isPolling={isSending}
        />
      )}

      {loginOverlay && (
        <LoginCode onReady={handleLoginReady} onFailed={handleLoginFailed} />
      )}

      <CampaignList onCampaignRestarted={handleCampaignRestarted} />
    </div>
  )
}

export default App