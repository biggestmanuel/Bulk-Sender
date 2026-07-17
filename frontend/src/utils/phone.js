// Single source of truth for phone validation on the frontend.
// Mirrors backend/sender/views.py's PHONE_RE exactly — keep these in sync.
export function isValidPhone(phone) {
  const cleaned = (phone ?? '').replace(/[\s\-()]/g, '')
  const regex = /^\+?[0-9]{10,15}$/
  return regex.test(cleaned)
}