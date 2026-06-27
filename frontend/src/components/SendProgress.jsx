function SendProgress({ total, sent, failed }) {
  const completed = sent + failed
  const percent = total > 0 ? Math.round((completed / total) * 100) : 0
  const barColor = failed > 0 ? '#d9363e' : 'green'

  return (
    <div style={{ marginTop: '20px', padding: '20px', border: '1px solid lightgray' }}>
      <h3>Sending Progress</h3>
      <p>
        {completed} / {total} processed ({percent}%) —{' '}
        <span style={{ color: 'green' }}>{sent} sent</span>
        {failed > 0 && (
          <span style={{ color: '#d9363e' }}> | {failed} failed</span>
        )}
      </p>
      <div style={{ background: '#eee', borderRadius: '6px', overflow: 'hidden', height: '20px', width: '100%' }}>
        <div
          style={{
            background: barColor,
            height: '100%',
            width: `${percent}%`,
            transition: 'width 0.3s'
          }}
        />
      </div>
    </div>
  )
}

export default SendProgress