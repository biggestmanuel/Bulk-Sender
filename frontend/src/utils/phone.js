// Phone number validation and normalization, backed by libphonenumber-js —
// replaces the old plain regex approach, which had no concept of country
// and would happily accept domestic-format numbers (e.g. a Nigerian number
// starting with 0) that can't actually be dialed on WhatsApp without a
// country code.
//
// Three possible outcomes for any input:
//   - "valid"          — has a + and parses as a real number. Ready to use.
//   - "needs-country"  — no + present at all. We can't guess which country
//                        this domestic-format number belongs to, so the UI
//                        must ask the person to pick one.
//   - "invalid"        — has a + but doesn't parse as a real number for
//                        that country code (wrong digit count, bad prefix, etc).
import { parsePhoneNumberFromString, isValidPhoneNumber, getCountries, getCountryCallingCode } from 'libphonenumber-js'

export function classifyPhone(phone) {
  const raw = (phone ?? '').trim()
  if (!raw) return { status: 'invalid', e164: null }

  if (!raw.startsWith('+')) {
    return { status: 'needs-country', e164: null }
  }

  try {
    if (isValidPhoneNumber(raw)) {
      const parsed = parsePhoneNumberFromString(raw)
      return { status: 'valid', e164: parsed.number }
    }
  } catch {
    // fall through to invalid
  }
  return { status: 'invalid', e164: null }
}

// Used once the person has picked a country for a "needs-country" number —
// combines the country's calling code with the digits they already typed.
export function resolveWithCountry(phone, countryCode) {
  const digitsOnly = (phone ?? '').replace(/[^0-9]/g, '')
  if (!digitsOnly) return { status: 'invalid', e164: null }

  try {
    const parsed = parsePhoneNumberFromString(digitsOnly, countryCode)
    if (parsed && parsed.isValid()) {
      return { status: 'valid', e164: parsed.number }
    }
  } catch {
    // fall through to invalid
  }
  return { status: 'invalid', e164: null }
}

// Kept for any code that just wants a quick yes/no rather than the full
// classification (e.g. summary counts).
export function isValidPhone(phone) {
  return classifyPhone(phone).status === 'valid'
}

// Full list of countries for the search-as-you-type picker, with
// human-readable names and calling codes attached.
let _countryListCache = null
export function getCountryList() {
  if (_countryListCache) return _countryListCache

  const displayNames = new Intl.DisplayNames(['en'], { type: 'region' })
  _countryListCache = getCountries()
    .map((code) => {
      let name = code
      try {
        name = displayNames.of(code) || code
      } catch {
        // some regions have no display name (rare) — fall back to the code
      }
      return { code, name, callingCode: getCountryCallingCode(code) }
    })
    .sort((a, b) => a.name.localeCompare(b.name))

  return _countryListCache
}