// Lists the attachments on the campaign being sent, and lets an image be
// previewed.
//
// This exists because the attachment was uploaded, counted in the campaign list
// and sent to WhatsApp — but never shown. The endpoint, the ownership check and
// the serializer field were all already there and already tested; nothing
// consumed them, and the one helper that did exist (mediaUrl) returned a bare
// URL that would have 401'd in an <img src>, since the endpoint authenticates
// with a header rather than a cookie.

import { useEffect, useState } from 'react'

import { fetchCampaignMedia } from '../api'

const IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']

function isImage(name = '') {
  const lower = name.toLowerCase()
  return IMAGE_EXTENSIONS.some((ext) => lower.endsWith(ext))
}

function humanSize(bytes) {
  const n = Number(bytes)
  if (!Number.isFinite(n) || n < 0) return 'unknown size'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export default function CampaignMedia({ campaignId, files = [] }) {
  const [openId, setOpenId] = useState(null)
  const [previewUrl, setPreviewUrl] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (openId == null) return undefined

    let objectUrl = null
    let cancelled = false

    fetchCampaignMedia(campaignId, openId)
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setPreviewUrl(objectUrl)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || 'Could not load the attachment.')
      })

    return () => {
      cancelled = true
      // Revoking on close/unmount, otherwise every preview leaks the whole
      // file for the lifetime of the tab.
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [campaignId, openId])

  if (!files.length) return null

  const toggle = (id) => {
    setError('')
    setPreviewUrl(null)
    setOpenId((current) => (current === id ? null : id))
  }

  return (
    <section className="bs-card" style={{ marginTop: 'var(--space-lg)' }}>
      <h2 style={{ margin: '0 0 var(--space-md) 0', fontSize: '15px' }}>
        Attachments ({files.length})
      </h2>

      <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: '6px' }}>
        {files.map((file) => (
          <li
            key={file.id}
            style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}
          >
            <span style={{ fontSize: '13px' }}>{file.name}</span>
            <span className="bs-badge bs-badge-muted">{humanSize(file.size)}</span>
            {isImage(file.name) && (
              <button
                type="button"
                className="bs-btn bs-btn-secondary"
                style={{ padding: '2px 8px', fontSize: '12px' }}
                aria-expanded={openId === file.id}
                onClick={() => toggle(file.id)}
              >
                {openId === file.id ? 'Hide' : 'Preview'}
              </button>
            )}
          </li>
        ))}
      </ul>

      {error && (
        <p style={{ marginTop: 'var(--space-md)', fontSize: '13px', color: 'var(--danger)' }}>
          {error}
        </p>
      )}

      {previewUrl && (
        <img
          src={previewUrl}
          alt="Campaign attachment preview"
          style={{ marginTop: 'var(--space-md)', maxWidth: '100%', borderRadius: '4px' }}
        />
      )}
    </section>
  )
}