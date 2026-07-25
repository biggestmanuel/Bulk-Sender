function SendProgress({ total, sent, failed }) {
  const completed = sent + failed
  const percent = total > 0 ? Math.round((completed / total) * 100) : 0
  const hasFailures = failed > 0

  // Signature pace-tick row: caps at 40 ticks so huge campaigns (up to
  // 5,000 contacts) don't render thousands of DOM nodes. Each tick then
  // represents a slice of the total rather than one contact each.
  const tickCount = Math.min(total, 40) || 1
  const doneTicks = Math.round((completed / (total || 1)) * tickCount)
  const failedTicks = Math.round((failed / (total || 1)) * tickCount)

  return (
    <div className="bs-card" style={{ marginTop: 'var(--space-lg)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 'var(--space-md)' }}>
        <h2 style={{ margin: 0 }}>Sending</h2>
        <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
          {completed} / {total} · {percent}%
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

      <div style={{ display: 'flex', gap: '8px' }}>
        <span className="bs-badge bs-badge-success">{sent} sent</span>
        {hasFailures && <span className="bs-badge bs-badge-danger">{failed} failed</span>}
        {!hasFailures && completed < total && <span className="bs-badge bs-badge-muted">{total - completed} remaining</span>}
      </div>
    </div>
  )
}

export default SendProgress
