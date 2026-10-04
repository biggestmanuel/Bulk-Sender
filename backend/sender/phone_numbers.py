r"""
Server-side phone number validation and normalization.

The frontend already validates with libphonenumber-js, but that was the
ONLY validation. The backend then applied a loose regex
(`^\+?[0-9]{10,15}$`) that accepted things WhatsApp can never deliver to:

    +2341          -> 4 digits, matches, silently dropped at send time
    +1234567890123 -> 13 digits of nonsense, matches
    08012345678    -> no country code; "valid" to the regex, undeliverable

Every rejected number was then SILENTLY DROPPED in views.create_campaign —
the person saw a successful campaign creation and a progress bar, and had
no idea a third of their list had vanished.

Normalization also happens here rather than only in the UI, because padded
numbers ("+234 816-123-4765") are extremely common in exported spreadsheets
and used to be rejected by the regex outright.

Install phonenumbers for full validation:

    python -m pip install phonenumbers

Without it, validation degrades to a structural length check so the app
still works, just without country-aware verification.
"""

import re

try:  # pragma: no cover - depends on environment
    import phonenumbers
    from phonenumbers import NumberParseException
    from phonenumbers.phonenumberutil import PhoneNumberFormat

    PHONENUMBERS_AVAILABLE = True
except ImportError:  # pragma: no cover
    phonenumbers = None
    PHONENUMBERS_AVAILABLE = False


# Formatting characters that show up constantly in exported spreadsheets and
# get silently mangled if left in place. Note we do NOT strip '+' — it is the
# signal that a country code is already present.
PADDING_CHARS = re.compile(r'[\s\-().\u00a0\u200b\u202f]')
NON_DIGITS = re.compile(r'\D')

# Structural bounds for E.164. Nothing real is shorter than ~7 digits and
# E.164 caps at 15.
MIN_DIGITS = 7
MAX_DIGITS = 15


class PhoneValidationError(ValueError):
    """Raised when a phone number can't be normalized into a dialable form."""

    def __init__(self, original, reason):
        self.original = original
        self.reason = reason
        super().__init__(f'{original!r}: {reason}')


def normalize(raw):
    """
    Strips formatting without changing the number's identity.

    '+234 816-123-4765' -> '+2348161234765'
    '08012345678'       -> '08012345678'  (unchanged — we can't guess the
                           country, so we don't strip the leading 0 either;
                           the frontend's country picker handles those)

    Raises PhoneValidationError if the result isn't plausibly dialable.
    """
    if raw is None:
        raise PhoneValidationError(raw, 'is empty')

    text = str(raw).strip()
    if not text:
        raise PhoneValidationError(raw, 'is empty')

    # Strip formatting wherever it appears (including inside the number).
    body = PADDING_CHARS.sub('', text)

    if body.startswith(('+', '*')):
        # Leading + (or *) is meaningful — keep the body verbatim so interior
        # junk shows up as an error instead of being silently deleted.
        pass
    else:
        # No leading +, so the whole thing should be digits. Anything else
        # (a "Tel:" label, letters from a messy export) is noise; a leading
        # "+" buried mid-string is worth keeping.
        plus = body.find('+')
        if plus != -1:
            body = body[plus:]

    if not body:
        raise PhoneValidationError(raw, 'contains no digits')
    if '*' in body:
        raise PhoneValidationError(raw, 'contains a wildcard, which cannot be dialled')

    digits = NON_DIGITS.sub('', body)
    if not digits:
        raise PhoneValidationError(raw, 'contains no digits')
    if len(digits) < MIN_DIGITS:
        raise PhoneValidationError(raw, f'has only {len(digits)} digits')
    if len(digits) > MAX_DIGITS:
        raise PhoneValidationError(
            raw, f'has {len(digits)} digits, more than the {MAX_DIGITS}-digit international maximum'
        )

    return body


def is_valid(raw):
    """True if `raw` is both structurally sane and a real dialable number."""
    try:
        to_e164(raw)
    except PhoneValidationError:
        return False
    return True


def to_e164(raw):
    """
    Normalizes and returns canonical E.164 form (e.g. '+2348161234765').

    With phonenumbers installed this also verifies length and prefix against
    the country's numbering plan — the check that catches '+2341' and
    '+1234567890123'. Without it, only the structural check from normalize()
    applies.

    A number with no leading '+' is rejected: there is no way to know which
    country a bare national format belongs to, and guessing would produce a
    number that looks valid and silently reaches the wrong person. The
    frontend's country picker resolves those before they get here.
    """
    cleaned = normalize(raw)

    if not cleaned.startswith('+'):
        raise PhoneValidationError(
            raw,
            'has no country code — add a + and the country code, e.g. +2348161234765',
        )

    if not PHONENUMBERS_AVAILABLE:
        return cleaned

    try:
        parsed = phonenumbers.parse(cleaned, None)
    except NumberParseException:
        raise PhoneValidationError(raw, 'is not a recognisable phone number')

    if not phonenumbers.is_possible_number(parsed):
        raise PhoneValidationError(raw, 'has an impossible length for its country code')

    if not phonenumbers.is_valid_number(parsed):
        raise PhoneValidationError(raw, 'is not a valid number for its country code')

    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)


def country_for(raw):
    """
    Returns (country_display_name, calling_code) for a phone number.

    Used by the automation to auto-select the right country in WhatsApp's
    "Link with phone number" dialog before submitting the national number.
    Submitting under the wrong country produces a confusing "code never
    arrives" failure, so this has to be right.

    Returns (None, '') when the country can't be determined.
    """
    cleaned = normalize(raw)
    digits = NON_DIGITS.sub('', cleaned)

    if PHONENUMBERS_AVAILABLE:
        try:
            parsed = phonenumbers.parse(cleaned, None)
            region = phonenumbers.region_code_for_number(parsed)
            if region:
                name = _region_display_name(parsed, region)
                return name, str(parsed.country_code)
        except NumberParseException:
            # Already validated by to_e164() on the way in; this only guards
            # against a library edge case. Narrowed from `except Exception`
            # so a typo'd attribute name can't be masked as "unknown country".
            pass
        return None, ''

    return _country_from_fallback_table(digits)


def _region_display_name(parsed, region):
    """
    Human-readable country name for the WhatsApp country dropdown.

    Deliberately country_name_for_number, NOT description_for_number: the
    latter returns a city or state ("San Francisco, CA") for landline-range
    numbers, which would not match any option in WhatsApp's country list.

    If the geocoder dataset has nothing (it ships separately and can lag
    new country-code assignments), falls back to the region code so the
    selector still has something clickable.
    """
    try:
        from phonenumbers import geocoder

        name = geocoder.country_name_for_number(parsed, 'en')
        if name:
            return name
    except Exception:
        pass
    return region


# Used only when phonenumbers isn't installed. Matching walks this
# longest-first so a 1-digit code can't shadow a 4-digit one (e.g. '1'
# vs '1242').
_FALLBACK_COUNTRIES = {
    '971': 'United Arab Emirates',
    '966': 'Saudi Arabia',
    '972': 'Israel',
    '961': 'Lebanon',
    '962': 'Jordan',
    '964': 'Iraq',
    '880': 'Bangladesh',
    '351': 'Portugal',
    '358': 'Finland',
    '420': 'Czechia',
    '380': 'Ukraine',
    '375': 'Belarus',
    '234': 'Nigeria',
    '233': 'Ghana',
    '254': 'Kenya',
    '250': 'Rwanda',
    '255': 'Tanzania',
    '256': 'Uganda',
    '260': 'Zambia',
    '263': 'Zimbabwe',
    '251': 'Ethiopia',
    '225': 'Ivory Coast',
    '221': 'Senegal',
    '232': 'Sierra Leone',
    '220': 'Gambia',
    '212': 'Morocco',
    '213': 'Algeria',
    '20': 'Egypt',
    '27': 'South Africa',
    '44': 'United Kingdom',
    '49': 'Germany',
    '31': 'Netherlands',
    '32': 'Belgium',
    '33': 'France',
    '34': 'Spain',
    '39': 'Italy',
    '41': 'Switzerland',
    '46': 'Sweden',
    '45': 'Denmark',
    '47': 'Norway',
    '353': 'Ireland',
    '48': 'Poland',
    '7': 'Russia',
    '90': 'Turkey',
    '91': 'India',
    '92': 'Pakistan',
    '94': 'Sri Lanka',
    '98': 'Iran',
    '93': 'Afghanistan',
    '60': 'Malaysia',
    '65': 'Singapore',
    '62': 'Indonesia',
    '63': 'Philippines',
    '66': 'Thailand',
    '84': 'Vietnam',
    '81': 'Japan',
    '82': 'South Korea',
    '86': 'China',
    '52': 'Mexico',
    '55': 'Brazil',
    '54': 'Argentina',
    '57': 'Colombia',
    '51': 'Peru',
    '56': 'Chile',
    '1242': 'Bahamas',
    '1243': 'Barbados',
    '1244': 'Antigua and Barbuda',
    '1245': 'Bermuda',
    '1246': 'British Virgin Islands',
    '1264': 'Anguilla',
    '1268': 'Montserrat',
    '1284': 'British Virgin Islands',
    '1347': 'United States',
    '1758': 'Saint Lucia',
    '1767': 'Dominica',
    '1787': 'Puerto Rico',
    '1809': 'Dominican Republic',
    '1849': 'Dominican Republic',
    '1876': 'Jamaica',
    '1888': 'Saint Kitts and Nevis',
    '1899': 'Trinidad and Tobago',
    '1': 'United States',
    '61': 'Australia',
    '64': 'New Zealand',
}


def _country_from_fallback_table(digits):
    for code in sorted(_FALLBACK_COUNTRIES, key=len, reverse=True):
        if digits.startswith(code):
            return _FALLBACK_COUNTRIES[code], code
    return None, ''