import { useCallback, useEffect, useRef, useState } from 'react'
import { deleteCampaign, listCampaigns, retryCampaign, startSending } from '../api'

// Status badge styling, keyed by the backend's Campaign.Status values.
const STATUS_META = {
  draft: { label: 'Draft', className: 'bs-badge bs-badge-muted' },
  queued: { label: 'Starting', className: 'bs-badge bs-badge-muted' },
  running: { label: 'Sending', className: 'bs-badge bs-badge-success' },
  completed: { label: 'Completed', className: 'bs-badge bs-badge-success' },
  failed: { label: 'Failed', className: 'bs-badge bs-badge-danger' },
}

function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function CampaignRow({ campaign, onRetry, onDelete, busyId }) {
  const meta = STATUS_META[campaign.status] || {
    label: campaign.status,
    className: 'bs-badge bs-badge-muted',
  }

  const { total_contacts: total, sent_contacts: sent, failed_contacts: failed } = campaign

  return (
    <tr style={{ borderTop: '1px solid var(--border)' }}>
      <td style={{ padding: '10px 12px', textAlign: 'left' }}>
        <strong style={{ display: 'block', fontSize: '14px' }}>{campaign.name}</strong>
        <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
          {formatDate(campaign.created_at)} · {campaign.send_mode === 'instant' ? 'fast' : 'paced'}
          {campaign.media_count > 0 && ` · ${campaign.media_count} attachment(s)`}
        </span>
      </td>

      <td style={{ padding: '10px 12px', textAlign: 'left' }}>
        <span className={meta.className}>{meta.label}</span>
      </td>

      <td style={{ padding: '10px 12px', textAlign: 'left', whiteSpace: 'nowrap' }}>
        {sent} sent
        {failed > 0 && (
          <span style={{ color: 'var(--danger)', marginLeft: '8px' }}>{failed} failed</span>
        )}
        <span style={{ color: 'var(--text-muted)', marginLeft: '8px' }}>of {total}</span>
      </td>

      <td style={{ padding: '10px 12px', textAlign: 'right', whiteSpace: 'nowrap' }}>
        {campaign.can_retry && (
          <button
            onClick={() => onRetry(campaign)}
            disabled={busyId === campaign.id}
            className="bs-btn bs-btn-primary"
            style={{ padding: '4px 10px', fontSize: '12px', marginRight: '6px' }}
          >
            {busyId === campaign.id ? 'Working…' : `Retry ${failed}`}
          </button>
        )}
        <button
          onClick={() => onDelete(campaign)}
          disabled={busyId === campaign.id}
          className="bs-btn bs-btn-danger"
          style={{ padding: '4px 10px', fontSize: '12px' }}
        >
          Delete
        </button>
      </td>
    </tr>
  )
}

/**
 * Campaign history.
 *
 * Campaigns were previously reachable only from the progress view of the
 * session that created them — reloading the page or coming back the next day
 * meant there was no way to find a previous campaign, let alone retry the ones
 * that failed partway through.
 */
function CampaignList({ onCampaignRestarted }) {
  const [campaigns, setCampaigns] = useState([])
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [hasNext, setHasNext] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async (targetPage = 1) => {
    setLoading(true)
    setError('')
    try {
      const data = await listCampaigns({ page: targetPage, pageSize: 10 })
      setCampaigns(data.campaigns || [])
      setTotal(data.total || 0)
      setHasNext(Boolean(data.has_next))
      setPage(targetPage)
    } catch (err) {
      setError(err.message || 'Could not load your campaigns.')
    } finally {
      setLoading(false)
    }
  }, [])

  // Runs on mount and whenever `load` changes (never, since it's memoized
  // with no dependencies). The initial render flag keeps the ref load from
  // firing twice under StrictMode's double-invoke.
  const didLoad = useRef(false)

  useEffect(() => {
    if (didLoad.current) return
    didLoad.current = true
    load(1)
  }, [load])

  /**
   * Retry is two calls by design: the reset is cheap and observable, and the
   * restart then goes through the same guards (already sending, quota) as any
   * other start.
   */
  async function handleRetry(campaign) {
    setBusyId(campaign.id)
    setError('')
    setNotice('')
    try {
      const result = await retryCampaign(campaign.id)

      if (result.reset_count === 0) {
        setNotice('There was nothing left to retry on that campaign.')
        await load(page)
        return
      }

      await startSending(campaign.id)
      setNotice(
        `Retrying ${result.reset_count} contact(s) from "${campaign.name}". ` +
          'WhatsApp may ask you to confirm the login again.'
      )
      await load(page)
      onCampaignRestarted?.(campaign.id)
    } catch (err) {
      setError(err.message || 'Could not retry that campaign.')
    } finally {
      setBusyId(null)
    }
  }

  async function handleDelete(campaign) {
    if (
      !window.confirm(
        `Delete "${campaign.name}"? This permanently removes it and its ${campaign.total_contacts} contact(s).`
      )
    ) {
      return
    }

    setBusyId(campaign.id)
    setError('')
    setNotice('')
    try {
      await deleteCampaign(campaign.id)
      // Stepping back a page avoids landing on an empty final page.
      const target = campaigns.length === 1 && page > 1 ? page - 1 : page
      await load(target)
    } catch (err) {
      setError(err.message || 'Could not delete that campaign.')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="bs-card" style={{ marginTop: 'var(--space-xl)' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          marginBottom: 'var(--space-md)',
          flexWrap: 'wrap',
          gap: '8px',
        }}
      >
        <h2 style={{ margin: 0 }}>
          {total > 0 ? `Your campaigns (${total})` : 'Your campaigns'}
        </h2>
        <button
          onClick={() => load(page)}
          disabled={loading}
          className="bs-btn bs-btn-secondary"
          style={{ padding: '6px 12px', fontSize: '12px' }}
        >
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {error && (
        <p style={{ color: 'var(--danger)', fontSize: '13px', marginBottom: 'var(--space-md)' }}>
          {error}
        </p>
      )}

      {notice && (
        <p
          style={{
            color: 'var(--text-primary)',
            background: 'var(--accent-bg)',
            fontSize: '13px',
            padding: '10px 12px',
            borderRadius: 'var(--radius-sm)',
            marginBottom: 'var(--space-md)',
          }}
        >
          {notice}
        </p>
      )}

      {!loading && campaigns.length === 0 && (
        <p style={{ color: 'var(--text-secondary)', fontSize: '13px', margin: 0 }}>
          No campaigns yet. Upload a CSV above to create your first one.
        </p>
      )}

      {campaigns.length > 0 && (
        <>
          <div
            style={{
              maxHeight: '420px',
              overflowY: 'auto',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
            }}
          >
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
              <thead>
                <tr
                  style={{
                    background: 'var(--bg-card-muted)',
                    position: 'sticky',
                    top: 0,
                    display: 'table',
                    width: '100%',
                  }}
                >
                  <th
                    style={{
                      textAlign: 'left',
                      padding: '10px 12px',
                      color: 'var(--text-secondary)',
                      fontWeight: 500,
                    }}
                  >
                    Campaign
                  </th>
                  <th
                    style={{
                      textAlign: 'left',
                      padding: '10px 12px',
                      color: 'var(--text-secondary)',
                      fontWeight: 500,
                    }}
                  >
                    Status
                  </th>
                  <th
                    style={{
                      textAlign: 'left',
                      padding: '10px 12px',
                      color: 'var(--text-secondary)',
                      fontWeight: 500,
                    }}
                  >
                    Progress
                  </th>
                  <th style={{ padding: '10px 12px' }} />
                </tr>
              </thead>
              <tbody>
                {campaigns.map((campaign) => (
                  <CampaignRow
                    key={campaign.id}
                    campaign={campaign}
                    onRetry={handleRetry}
                    onDelete={handleDelete}
                    busyId={busyId}
                  />
                ))}
              </tbody>
            </table>
          </div>

          {(page > 1 || hasNext) && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                marginTop: 'var(--space-md)',
              }}
            >
              <button
                onClick={() => load(page - 1)}
                disabled={page <= 1 || loading}
                className="bs-btn bs-btn-secondary"
                style={{ padding: '6px 14px', fontSize: '12px' }}
              >
                Newer
              </button>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                Page {page}
              </span>
              <button
                onClick={() => load(page + 1)}
                disabled={!hasNext || loading}
                className="bs-btn bs-btn-secondary"
                style={{ padding: '6px 14px', fontSize: '12px' }}
              >
                Older
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default CampaignList