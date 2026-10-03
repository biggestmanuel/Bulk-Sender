import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SendProgress from './SendProgress'

function renderProgress(props) {
  return render(<SendProgress total={10} sent={0} failed={0} {...props} />)
}

describe('SendProgress', () => {
  it('shows the count and percentage', () => {
    const { container } = renderProgress({ total: 10, sent: 4, failed: 1 })
    // Completed is sent+failed, so 5 of 10 here.
    expect(container.textContent).toContain('5 / 10 · 50%')
  })

  it('reports sent and failed counts while polling', () => {
    renderProgress({ total: 10, sent: 6, failed: 2, isPolling: true })
    expect(screen.getByText('6 sent')).toBeInTheDocument()
    expect(screen.getByText('2 failed')).toBeInTheDocument()
  })

  it('shows the remaining count once polling has stopped', () => {
    // A count of "remaining" while the send is still in flight invites the
    // reader to think it's finished-but-short.
    renderProgress({ total: 10, sent: 6, failed: 2, isPolling: false })
    expect(screen.getByText('2 remaining')).toBeInTheDocument()
  })

  it('caps the tick row so large campaigns stay cheap to render', () => {
    // A 5,000-contact campaign must not create 5,000 DOM nodes.
    const { container } = renderProgress({ total: 5000, sent: 100 })
    const ticks = container.querySelectorAll('.bs-pace-tick')
    expect(ticks.length).toBeLessThanOrEqual(40)
  })

  it('survives an empty campaign', () => {
    expect(() => renderProgress({ total: 0, sent: 0, failed: 0 })).not.toThrow()
  })

  /**
   * The component is purely cosmetic, so bad numbers from a server bug
   * shouldn't be able to crash the render or produce NaN in the DOM.
   */
  it('clamps inconsistent counts instead of rendering nonsense', () => {
    const { container } = renderProgress({ total: 10, sent: 99, failed: 99 })
    expect(container.textContent).not.toMatch(/NaN|Infinity/)
  })

  it('ignores negative counts', () => {
    const { container } = renderProgress({ total: -5, sent: -2, failed: -1 })
    expect(container.textContent).not.toMatch(/NaN|Infinity/)
  })

  it('switches heading once polling stops', () => {
    const { rerender } = renderProgress({ total: 10, sent: 10, isPolling: true })
    expect(screen.getByRole('heading', { name: 'Sending' })).toBeInTheDocument()

    rerender(<SendProgress total={10} sent={10} failed={0} isPolling={false} />)
    expect(screen.getByRole('heading', { name: 'Finished' })).toBeInTheDocument()
  })

  it('shows the server failure detail when a campaign failed', () => {
    renderProgress({
      total: 10,
      sent: 2,
      failed: 8,
      campaignStatus: 'failed',
      statusDetail: 'WhatsApp login did not complete.',
    })
    expect(screen.getByText('WhatsApp login did not complete.')).toBeInTheDocument()
  })

  it('announces updates for screen readers', async () => {
    const user = userEvent.setup()
    const { container } = renderProgress({ total: 4, sent: 1 })
    expect(container.querySelector('[aria-live="polite"]')).toBeInTheDocument()
    await user.click(container)
  })
})