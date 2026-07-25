import { useState, useMemo, useRef, useEffect } from 'react'
import { getCountryList } from '../utils/phone'

// Small search-as-you-type country picker. Typing "U" filters down to
// United States, United Kingdom, Uruguay, Uzbekistan, etc. — used wherever
// we need the person to tell us which country a country-code-less phone
// number belongs to, since we can't safely guess that ourselves.
function CountryPicker({ onSelect, onCancel }) {
  const [query, setQuery] = useState('')
  const inputRef = useRef(null)
  const countries = useMemo(() => getCountryList(), [])

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return countries.slice(0, 8)
    return countries.filter((c) => c.name.toLowerCase().startsWith(q)).slice(0, 8)
  }, [query, countries])

  return (
    <div style={{ position: 'relative' }}>
      <input
        ref={inputRef}
        className="bs-input"
        placeholder="Search for a country…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Escape') onCancel() }}
      />

      <div
        className="bs-card"
        style={{
          position: 'absolute',
          top: 'calc(100% + 4px)',
          left: 0,
          right: 0,
          zIndex: 10,
          padding: 'var(--space-xs)',
          maxHeight: '220px',
          overflowY: 'auto',
        }}
      >
        {filtered.length === 0 && (
          <p style={{ padding: 'var(--space-sm)', fontSize: '13px', color: 'var(--text-muted)' }}>
            No countries match "{query}"
          </p>
        )}
        {filtered.map((c) => (
          <button
            key={c.code}
            onClick={() => onSelect(c)}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              width: '100%',
              textAlign: 'left',
              padding: '8px 10px',
              border: 'none',
              background: 'transparent',
              cursor: 'pointer',
              borderRadius: 'var(--radius-sm)',
              fontSize: '13px',
              color: 'var(--text-primary)',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-card-muted)' }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
          >
            <span>{c.name}</span>
            <span style={{ color: 'var(--text-muted)' }}>+{c.callingCode}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

export default CountryPicker
