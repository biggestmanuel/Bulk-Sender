"""Tests for phone number normalization, validation, and country resolution."""

from django.test import SimpleTestCase

from sender import phone_numbers as pn
from sender.phone_numbers import PhoneValidationError, country_for, is_valid, normalize, to_e164


class NormalizeTests(SimpleTestCase):
    def test_strips_common_padding(self):
        # Real-world CSV exports arrive like this constantly.
        self.assertEqual(normalize('+234 816-123-4765'), '+2348161234765')
        self.assertEqual(normalize('+234 (816) 123.4765'), '+2348161234765')
        self.assertEqual(normalize('  +1 415 555 2671  '), '+14155552671')
        self.assertEqual(normalize('+234\u00a0816\u200b123 4765'), '+2348161234765')

    def test_preserves_leading_zero_for_countryless_numbers(self):
        """
        We can't guess the country, so we must not strip the leading zero —
        that would silently turn 08012345678 into 8012345678 and then hand
        WhatsApp a wrong number that looks plausible.
        """
        self.assertEqual(normalize('08012345678'), '08012345678')

    def test_tolerates_a_leading_label(self):
        self.assertEqual(normalize('Tel: +2348161234765'), '+2348161234765')

    def test_rejects_empty(self):
        for value in (None, '', '   '):
            with self.assertRaises(PhoneValidationError):
                normalize(value)

    def test_rejects_too_few_and_too_many_digits(self):
        with self.assertRaises(PhoneValidationError) as ctx:
            normalize('+2341')
        self.assertIn('4 digits', ctx.exception.reason)

        with self.assertRaises(PhoneValidationError) as ctx:
            normalize('+23481612347650000')
        self.assertIn('maximum', ctx.exception.reason)

    def test_rejects_wildcards(self):
        with self.assertRaises(PhoneValidationError) as ctx:
            normalize('+2348161234***')
        self.assertIn('wildcard', ctx.exception.reason)

    def test_rejects_no_digits(self):
        with self.assertRaises(PhoneValidationError):
            normalize('abc')


class ToE164Tests(SimpleTestCase):
    def test_returns_canonical_form(self):
        self.assertEqual(to_e164('+2348161234765'), '+2348161234765')
        self.assertEqual(to_e164('+234 816 123 4765'), '+2348161234765')

    def test_rejects_numbers_without_a_country_code(self):
        """
        A bare national number can't be resolved server-side — there's no way
        to know which country it belongs to, and guessing would deliver to the
        wrong person. The frontend's country picker resolves these first, so
        anything reaching here without a + is a bug or a crafted request.
        """
        with self.assertRaises(PhoneValidationError) as ctx:
            to_e164('08012345678')
        self.assertIn('country code', ctx.exception.reason)

    def test_rejects_too_short_for_country(self):
        with self.assertRaises(PhoneValidationError):
            to_e164('+23481')

    def test_rejects_nonsense_digit_runs(self):
        # The old regex accepted all of these; they'd fail silently at send
        # time and the campaign would report success.
        for value in ('+1234567890123', '+999123456789', '+1111111111111'):
            with self.assertRaises(PhoneValidationError, msg=value):
                to_e164(value)

    def test_is_valid_matches_to_e164(self):
        self.assertTrue(is_valid('+2348161234765'))
        self.assertFalse(is_valid('+2341'))
        self.assertFalse(is_valid(''))
        self.assertFalse(is_valid(None))

    def test_error_reason_is_actionable(self):
        with self.assertRaises(PhoneValidationError) as ctx:
            to_e164('+2341')
        self.assertRegex(ctx.exception.reason, r'digits|length')


class CountryForTests(SimpleTestCase):
    """
    The automation selects the country in WhatsApp's login dialog from this.
    Getting it wrong produces a code that never arrives, which is close to
    undiagnosable from the user's side.
    """

    def test_resolves_country_codes(self):
        cases = {
            '+2348161234765': '234',
            '+233201234567': '233',
            '+254712345678': '254',
            '+14155552671': '1',
            '+447911123456': '44',
            '+27721234567': '27',
            '+971501234567': '971',
        }
        for number, expected_code in cases.items():
            self.assertEqual(country_for(number)[1], expected_code, f'{number} -> code')

    def test_resolves_display_names_for_main_countries(self):
        # Names the geocoder is reliable for. Deliberately NOT asserting on
        # every number — see test_range_based_names_can_disagree_with_region.
        for number, expected in (
            ('+2348161234765', 'Nigeria'),
            ('+254712345678', 'Kenya'),
            ('+971501234567', 'United Arab Emirates'),
        ):
            self.assertEqual(country_for(number)[0], expected, number)

    def test_range_based_names_can_disagree_with_region(self):
        """
        +44 7911 is a Guernsey mobile range, so the geocoder reports
        "Guernsey" for a number whose region is GB.

        This is a documented limitation rather than a bug: the calling code
        (44) is always right, and the automation verifies the field accepted
        the value and falls back to the international form when the country
        selection doesn't work. This test exists so the behaviour stays
        visible if the resolution logic is ever changed.
        """
        name, code = country_for('+447911123456')
        self.assertEqual(code, '44')
        self.assertIn(name, {'United Kingdom', 'Guernsey', 'Jersey', 'Isle of Man', 'GB'})

    def test_longer_calling_codes_win_over_shorter_prefixes(self):
        """
        '971' must not be matched as '9'. Prefix collisions are the classic
        failure mode in any hand-maintained calling-code table.
        """
        name, code = country_for('+971501234567')
        self.assertEqual(code, '971')
        self.assertEqual(name, 'United Arab Emirates')

    def test_nanp_sub_codes_resolve_to_the_right_country(self):
        """
        +1 242 is the Bahamas, not the US. The calling code is legitimately
        '1' for both, so correctness here depends on the national prefix —
        which is exactly what the fallback table's longest-first walk gives.
        """
        self.assertEqual(country_for('+12424259876')[0], 'Bahamas')
        _, table_code = pn._country_from_fallback_table('12424259876')
        self.assertEqual(table_code, '1242')

    def test_unknown_prefix_returns_none(self):
        name, code = country_for('+999123456789')
        self.assertEqual(code, '')
        self.assertIsNone(name)

    def test_fallback_table_matches_phonenumbers(self):
        """The no-phonenumbers fallback shouldn't disagree wildly."""
        if not pn.PHONENUMBERS_AVAILABLE:
            self.skipTest('phonenumbers not installed')

        for number in ('+2348161234765', '+447911123456', '+14155552671'):
            _, from_lib = country_for(number)
            _, from_table = pn._country_from_fallback_table(
                ''.join(ch for ch in number if ch.isdigit())
            )
            self.assertEqual(from_lib, from_table, number)

    def test_fallback_table_has_no_duplicate_keys(self):
        """
        A duplicate key in that dict silently drops a country — Python keeps
        the last value — so it's checked at parse time rather than trusted.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(pn))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') == '_FALLBACK_COUNTRIES':
                keys = [k.value for k in node.value.keys]
                self.assertEqual(len(keys), len(set(keys)), f'duplicate keys in table: {keys}')
                return
        self.fail('_FALLBACK_COUNTRIES literal not found in source')
