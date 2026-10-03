// Progress readout for a campaign send.
//
// Assumes the counts it is given are consistent (sent + failed <= total).
// If a server bug ever violates that, the tick row can render nonsense — so
// the fractions are clamped rather than left to produce NaN or a negative
// bar. Cheap insurance on a purely cosmetic component.
function SendProgress({
  total,
  sent,
  failed,
  campaignStatus,
  statusDetail,
  isPolling = false,
}) {
  const safeTotal = Math.max(0, total || 0)
  const safeSent = Math.min(Math.max(0, sent || 0), safeTotal)
  const safeFailed = Math.min(Math.max(0, failed || 0), safeTotal - safeSent)

  const completed = safeSent + safeFailed
  const percent = safeTotal > 0 ? Math.round((completed / safeTotal) * 100) : 0
  const hasFailures = safeFailed > 0
  const remaining = safeTotal - completed

  // Caps at 40 ticks so a 5,000-contact campaign doesn't render thousands of
  // DOM nodes. Each tick then represents a slice of the total rather than one
  // contact.
  const tickCount = Math.min(safeTotal, 40) || 1
  const doneTicks = Math.round((completed / (safeTotal || 1)) * tickCount)
  const failedTicks = Math.round((safeFailed / (safeTotal || 1)) * tickCount)

  const heading = isPolling ? 'Sending' : completed >= safeTotal ? 'Finished' : 'Sending'

  return (
    <div className="bs-card" style={{ marginTop: 'var(--space-lg)' }} aria-live="polite">
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          marginBottom: 'var(--space-md)',
        }}
      >
        <h2 style={{ margin: 0 }}>{heading}</h2>
        <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
          {completed} / {safeTotal} · {percent}%
        </span>
      </div>

      <div className="bs-pace" style={{ marginBottom: 'var(--space-md)' }} aria-hidden="true">
        {Array.from({ length: tickCount }).map((_, i) => {
          let className = 'bs-pace-tick'
          if (i < doneTicks - failedTicks) className += ' is-done'
          else if (i < doneTicks) className += ' is-failed'
          return <span key={i} className={className} />
        })}
      </div>

      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
        <span className="bs-badge bs-badge-success">{safeSent} sent</span>
        {hasFailures && <span className="bs-badge bs-badge-danger">{safeFailed} failed</span>}
        {!isPolling && remaining > 0 && (
          <span className="bs-badge bs-badge-muted">{remaining} remaining</span>
        )}
        {campaignStatus === 'failed' && statusDetail && (
          <span style={{ fontSize: '13px', color: 'var(--danger)' }}>{statusDetail}</span>
        )}
      </div>
    </div>
  )
}

export default SendProgress