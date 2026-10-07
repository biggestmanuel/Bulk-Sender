import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CampaignMedia from './CampaignMedia'
import { fetchCampaignMedia } from '../api'

vi.mock('../api', () => ({
  fetchCampaignMedia: vi.fn(),
}))

const IMAGE = { id: 1, name: 'holiday-photo.png', size: 2048 }
const VIDEO = { id: 2, name: 'clip.mp4', size: 5 * 1024 * 1024 }

describe('CampaignMedia', () => {
  beforeEach(() => {
    window.URL.createObjectURL = vi.fn(() => 'blob:preview')
    window.URL.revokeObjectURL = vi.fn()
  })

  it('renders nothing when there are no attachments', () => {
    const { container } = render(<CampaignMedia campaignId={1} files={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('lists each attachment with a readable size', () => {
    render(<CampaignMedia campaignId={1} files={[IMAGE, VIDEO]} />)

    expect(screen.getByText('holiday-photo.png')).toBeInTheDocument()
    expect(screen.getByText('clip.mp4')).toBeInTheDocument()
    expect(screen.getByText('2 KB')).toBeInTheDocument()
    expect(screen.getByText('5.0 MB')).toBeInTheDocument()
    expect(screen.getByText(/Attachments \(2\)/)).toBeInTheDocument()
  })

  it('offers a preview for images but not for other files', () => {
    render(<CampaignMedia campaignId={1} files={[IMAGE, VIDEO]} />)

    // Only the image gets a preview control.
    expect(screen.getAllByRole('button', { name: /preview/i })).toHaveLength(1)
  })

  it('fetches the bytes with auth rather than using a bare URL', async () => {
    const user = userEvent.setup()
    fetchCampaignMedia.mockResolvedValue(new Blob(['x'], { type: 'image/png' }))
    render(<CampaignMedia campaignId={7} files={[IMAGE]} />)

    await user.click(screen.getByRole('button', { name: /preview/i }))

    await waitFor(() => expect(fetchCampaignMedia).toHaveBeenCalledWith(7, 1))
  })

  it('shows the preview once loaded', async () => {
    const user = userEvent.setup()
    fetchCampaignMedia.mockResolvedValue(new Blob(['x'], { type: 'image/png' }))
    render(<CampaignMedia campaignId={1} files={[IMAGE]} />)

    await user.click(screen.getByRole('button', { name: /preview/i }))

    const img = await screen.findByAltText(/attachment preview/i)
    expect(img).toHaveAttribute('src', 'blob:preview')
  })

  it('revokes the object URL when closed, so the blob is not leaked', async () => {
    const user = userEvent.setup()
    fetchCampaignMedia.mockResolvedValue(new Blob(['x'], { type: 'image/png' }))
    render(<CampaignMedia campaignId={1} files={[IMAGE]} />)

    await user.click(screen.getByRole('button', { name: /preview/i }))
    await screen.findByAltText(/attachment preview/i)

    await user.click(screen.getByRole('button', { name: /hide/i }))

    await waitFor(() => expect(window.URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview'))
  })

  it('reports a failure instead of silently showing nothing', async () => {
    const user = userEvent.setup()
    fetchCampaignMedia.mockRejectedValue(new Error('Could not load the attachment.'))
    render(<CampaignMedia campaignId={1} files={[IMAGE]} />)

    await user.click(screen.getByRole('button', { name: /preview/i }))

    expect(await screen.findByText(/could not load the attachment/i)).toBeInTheDocument()
  })

  it('handles a missing or nonsensical size without printing NaN', () => {
    render(<CampaignMedia campaignId={1} files={[{ id: 3, name: 'x.png' }]} />)
    expect(screen.getByText('unknown size')).toBeInTheDocument()
  })
})