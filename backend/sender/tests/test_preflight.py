"""
Tests for the preflight management command and the selector constants it
depends on.

The command's value is that a broken selector is reported in ~30 seconds with
a specific message, instead of surfacing as a campaign that burns ten minutes
and fails with every contact marked failed. So the tests focus on: does it
detect each failure, and does it say which one.
"""

import io
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from sender import whatsapp_automation as wa


class SelectorConstantTests(SimpleTestCase):
    """
    The command and the send path share these constants. If they drift, the
    preflight would report green while sends fail.
    """

    def test_selector_constants_exist(self):
        for name in (
            'PHONE_LOGIN_LINK_SELECTOR',
            'PHONE_INPUT_SELECTOR',
            'NEXT_BUTTON_SELECTOR',
            'CHAT_LIST_SELECTOR',
            'SEND_ICON_SELECTOR',
            'PHONE_REJECTION_SELECTORS',
        ):
            self.assertTrue(getattr(wa, name), f'{name} is empty')

    def test_login_code_pattern_matches_the_observed_formats(self):
        """
        WhatsApp has rendered codes as ABCD-1234, ABCD 1234 and ABCD1234
        depending on version. All must match; anything longer must not.
        """
        for sample in ('ABCD-1234', 'ABCD 1234', 'ABCD1234', 'abcd-1234'):
            with self.subTest(sample=sample):
                match = wa.LOGIN_CODE_PATTERN.search(f'Enter code on phone\n{sample}')
                self.assertIsNotNone(match, sample)
                self.assertEqual(
                    ''.join(c for c in match.group(1) if c.isalnum()).upper(), 'ABCD1234'
                )

    def test_login_code_pattern_rejects_the_wrong_length(self):
        for sample in ('ABC-123', 'ABCDEFGHIJ', '123'):
            with self.subTest(sample=sample):
                self.assertIsNone(wa.LOGIN_CODE_PATTERN.search(f'Enter code on phone\n{sample}'))


class FakeLocator:
    """
    Models a Playwright locator closely enough for the command.

    A bare Mock isn't sufficient: the command calls `.first.wait_for(...)`,
    and a Mock's auto-attributes would return truthy for every selector, so
    every check would pass regardless of what the "page" actually contains.
    """

    def __init__(self, present=False, fail_wait=False):
        self.present = present
        self.fail_wait = fail_wait

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.present else 0

    def wait_for(self, state=None, timeout=None):
        if self.fail_wait or not self.present:
            raise RuntimeError(f'Timeout {timeout}ms exceeded')

    def evaluate(self, script):
        return None


class FakePage:
    """A page that exposes exactly the selectors in `present`."""

    def __init__(self, present=()):
        self.present = set(present)
        self.goto_urls = []
        self.failing_waits = set()

    def goto(self, url, timeout=None):
        self.goto_urls.append(url)

    def wait_for_load_state(self, state=None, timeout=None):
        return None

    def wait_for_selector(self, selector, timeout=None):
        if selector not in self.present:
            raise RuntimeError(f'Timeout {timeout}ms exceeded')

    def locator(self, selector):
        selector = str(selector)
        if selector in self.failing_waits:
            return FakeLocator(present=True, fail_wait=True)
        return FakeLocator(present=selector in self.present)


def holder_factory(holder):
    """
    What sync_playwright() should return: a real context manager.

    Patching with a MagicMock and configuring return_value/__enter__ does not
    work here — mock.patch's return_value is what the *call* produces, so the
    command's `with ... as playwright` would then invoke `__enter__` on a
    different object and get a fresh auto-Mock back.
    """
    class PlaywrightContext:
        def __enter__(self):
            return holder

        def __exit__(self, *exc_info):
            return False

    return PlaywrightContext()


class PreflightCommandTests(SimpleTestCase):
    """Drives the command against a fake browser."""

    def run_command(self, **kwargs):
        out = io.StringIO()
        call_command('whatsapp_preflight', stdout=out, stderr=out, **kwargs)
        return out.getvalue()

    def login_page(self):
        """Everything visible except the chat list, i.e. on the login screen."""
        return FakePage(present={
            wa.PHONE_LOGIN_LINK_SELECTOR,
            wa.PHONE_INPUT_SELECTOR,
            wa.NEXT_BUTTON_SELECTOR,
        })

    def logged_in_page(self):
        return FakePage(present={wa.CHAT_LIST_SELECTOR})

    def fake_playwright(self, page_factory, launch_error=None):
        """
        A stand-in for the playwright object, with an explicit chromium.

        A bare mock.Mock() won't do: `holder.chromium.launch_persistent_context`
        would auto-create attributes, and the real code path compares and
        formats values derived from them, which explodes on a MagicMock.
        """
        class FakeChromium:
            def __init__(self):
                self.launch_kwargs = None

            def launch_persistent_context(self, **kwargs):
                if launch_error:
                    raise launch_error
                self.launch_kwargs = kwargs

                class Context:
                    def __init__(self, page):
                        self.pages = [page]

                    def close(self):
                        return None

                return Context(page_factory())

        chromium = FakeChromium()
        # Records the launch arguments, which one test inspects to confirm the
        # preflight never touches a real user's browser profile.
        self.last_chromium = chromium
        return mock.Mock(chromium=chromium)

    def run_with_page(self, page, **kwargs):
        holder = self.fake_playwright(lambda: page)

        with mock.patch(
            'playwright.sync_api.sync_playwright', return_value=holder_factory(holder)
        ), mock.patch.object(wa, '_browser_channel_for', return_value='chromium'):
            return self.run_command(**kwargs)

    # --- Failure detection -------------------------------------------------

    def test_reports_failure_when_the_page_will_not_load(self):
        page = self.login_page()

        def failing_goto(url, timeout=None):
            raise RuntimeError('net::ERR_CONNECTION_TIMED_OUT')

        page.goto = failing_goto

        output = self.run_with_page(page)

        self.assertIn('page loads', output)
        self.assertIn('could not load', output)
        self.assertIn('checks failed', output)

    def test_reports_failure_when_the_login_link_is_missing(self):
        """The exact scenario the command exists for: WhatsApp changed the page."""
        page = self.login_page()
        page.present.discard(wa.PHONE_LOGIN_LINK_SELECTOR)

        output = self.run_with_page(page)

        self.assertIn('login link', output)
        self.assertIn('WhatsApp may have removed', output)
        self.assertIn('checks failed', output)

    def test_reports_failure_when_the_phone_input_is_missing(self):
        page = self.login_page()
        page.present.discard(wa.PHONE_INPUT_SELECTOR)

        output = self.run_with_page(page)

        self.assertIn('phone input', output)
        self.assertIn('checks failed', output)

    def test_reports_failure_when_the_next_button_is_missing(self):
        page = self.login_page()
        page.present.discard(wa.NEXT_BUTTON_SELECTOR)

        output = self.run_with_page(page)

        self.assertIn('Next button', output)
        self.assertIn('checks failed', output)

    def test_reports_a_browser_launch_failure_clearly(self):
        with mock.patch('playwright.sync_api.sync_playwright') as sp:
            sp.return_value.__enter__.side_effect = Exception('Executable does not exist')

            with self.assertRaises(CommandError) as ctx:
                self.run_command()

        self.assertIn('Could not launch a browser', str(ctx.exception))
        # Mentions Xvfb, since that's the fix for a headless host.
        self.assertIn('Xvfb', str(ctx.exception))

    # --- Success paths -----------------------------------------------------

    def test_short_circuits_when_already_logged_in(self):
        """
        On a linked profile the login flow isn't visible, so the checks that
        need it must be skipped rather than reported as failures.
        """
        output = self.run_with_page(self.logged_in_page())

        self.assertIn('session is linked', output)
        self.assertIn('skipped: already linked', output)
        self.assertIn('All 6 checks passed', output)

    def test_passes_when_every_selector_matches(self):
        output = self.run_with_page(self.login_page(), number='+2348161234765')

        self.assertIn('login link', output)
        self.assertIn('phone input', output)
        self.assertIn('Next button', output)
        self.assertIn('Nigeria', output)
        self.assertIn('All', output)
        self.assertNotIn('checks failed', output)

    def test_warns_when_the_country_cannot_be_resolved(self):
        """
        An unresolvable country means the automation would submit under the
        wrong one, which shows up as a code that never arrives.
        """
        with mock.patch.object(wa, 'country_for', return_value=(None, '')):
            output = self.run_with_page(self.login_page(), number='+999123456789')

        self.assertIn('could not determine a country', output)

    def test_mentions_the_browser_it_chose(self):
        with mock.patch.object(wa, '_find_installed_browser', return_value='/usr/bin/google-chrome'):
            output = self.run_with_page(self.logged_in_page())

        self.assertIn('/usr/bin/google-chrome', output)

    def test_warns_when_no_local_browser_is_found(self):
        with mock.patch.object(wa, '_find_installed_browser', return_value=None):
            output = self.run_with_page(self.logged_in_page())

        self.assertIn('no local Chrome/Edge/Brave found', output)

    def test_does_not_touch_a_users_real_session(self):
        """
        Uses a throwaway profile, so running the check can't unlink a real
        WhatsApp account.
        """
        holder = self.fake_playwright(self.logged_in_page)

        with mock.patch('playwright.sync_api.sync_playwright', return_value=holder_factory(holder)), \
             mock.patch.object(wa, '_browser_channel_for', return_value='chromium'):
            self.run_command()

        launch_kwargs = self.last_chromium.launch_kwargs
        self.assertIsNotNone(launch_kwargs, 'the browser should have been launched')
        profile_dir = str(launch_kwargs['user_data_dir'])
        self.assertIn('wa-preflight', profile_dir)
        self.assertNotIn('whatsapp_session', profile_dir)
        self.assertNotIn('user_', profile_dir)
