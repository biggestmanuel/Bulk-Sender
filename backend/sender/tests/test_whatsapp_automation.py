"""
Tests for the Playwright automation layer.

These run against a fake Playwright rather than a real browser. That is
deliberate: the actual browser interaction cannot be asserted in CI, but
everything this module is responsible for *around* the browser — session
reuse, the retry-after-wipe path, pacing, phone-entry fallbacks, and status
settling — is pure logic and very much worth testing.
"""

from unittest import mock

from django.test import SimpleTestCase, override_settings

from sender import whatsapp_automation as wa


# --- Fake Playwright ------------------------------------------------------


class FakeLocator:
    def __init__(self, page, selector, visible=True):
        self.page = page
        self.selector = selector
        self.text = ''
        self.visible = visible
        self.clicked = False
        self.filled = None
        self.evaluations = []

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.visible else 0

    def click(self, **kwargs):
        if not self.visible:
            raise AssertionError(f'click on hidden {self.selector}')
        self.clicked = True
        return None

    def fill(self, value):
        self.filled = value
        page = self.page
        page.current_input = value
        page.rejected = value in page.phone_rejects
        # Model WhatsApp's validation message appearing/disappearing with the
        # value, since _phone_field_rejected() inspects the DOM for it.
        for selector in wa.PHONE_REJECTION_SELECTORS:
            page.locators[selector] = FakeLocator(
                page, selector, visible=page.rejected
            )

    def evaluate(self, script):
        self.evaluations.append(script)

    def is_visible(self):
        return self.visible

    def inner_text(self):
        # The real page's `body` locator yields the whole rendered text.
        if self.selector == 'body':
            return self.page.body_text
        return self.text

    def wait_for(self, state=None, timeout=None):
        if not self.visible:
            raise AssertionError(f'wait_for on missing {self.selector}')


class FakePage:
    """
    Minimal stand-in for a Playwright Page.

    Selectors asked for but never registered are treated as absent, which
    matches how a real DOM behaves. A test opts into an element by putting a
    visible FakeLocator in `page.locators`.
    """

    def __init__(self, body_text='', country_options=(), phone_rejects=()):
        self.body_text = body_text
        self.country_options = set(country_options)
        # Values modelled as rejected by WhatsApp, so the international-form
        # fallback can be exercised.
        self.phone_rejects = set(phone_rejects)
        self.current_input = None
        self.rejected = False
        self.locators = {}
        self.goto_urls = []
        self.screenshots = []
        # Flipped by tests that model a browser dying mid-campaign.
        self.dead = False

    def show(self, selector, text=''):
        """Registers a visible element for a selector."""
        loc = FakeLocator(self, selector, visible=True)
        loc.text = text
        self.locators[selector] = loc
        return loc

    def hide(self, selector):
        self.locators[selector] = FakeLocator(self, selector, visible=False)
        return self.locators[selector]

    def locator(self, selector):
        if selector not in self.locators:
            # Absent by default. Country dropdown options are visible only
            # when the test declares them.
            visible = False
            if '[role="option"]' in selector and 'has-text("' in selector:
                country = selector.split('has-text("')[1].split('"')[0]
                visible = country in self.country_options
            self.locators[selector] = FakeLocator(self, selector, visible=visible)
        return self.locators[selector]

    def set_extra_http_headers(self, headers):
        self.headers = headers

    def add_init_script(self, script):
        self.init_script = script

    def goto(self, url, timeout=None):
        self.goto_urls.append(url)

    def wait_for_load_state(self, state=None, timeout=None):
        pass

    def wait_for_selector(self, selector, timeout=None):
        if not self.locator(selector).visible:
            raise AssertionError(f'selector never appeared: {selector}')

    def wait_for_timeout(self, ms):
        pass

    def screenshot(self, path=None):
        self.screenshots.append(path)

    def title(self):
        """
        Models a live page for _browser_is_alive().

        A real browser raises here once its process is gone, which is the
        signal the send loop uses to stop early. Set `dead = True` to model
        the crash.
        """
        if self.dead:
            raise AssertionError('Target page, context or browser has been closed')
        return self.body_text

    def expect_file_chooser(self):
        raise AssertionError('file chooser not expected in this test')

    def click(self, selector, **kwargs):
        return self.locator(selector).click(**kwargs)


class FakeContext:
    def __init__(self, page):
        self.page = page
        self.closed = False
        self.pages = [page]

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


class FakePlaywright:
    def __init__(self, page):
        self.page = page
        self.contexts = []
        self.chromium = mock.Mock()
        self.chromium.launch_persistent_context.side_effect = lambda **kw: (
            self._launch(kw)
        )

    def _launch(self, kwargs):
        ctx = FakeContext(self.page)
        ctx.launch_kwargs = kwargs
        self.contexts.append(ctx)
        return ctx


def fake_playwright(page):
    """
    Returns (callable, holder).

    The callable matches `playwright_factory`: calling it yields a context
    manager whose `__enter__` is the fake Playwright object.
    """
    holder = FakePlaywright(page)

    class Factory:
        def __enter__(self):
            return holder

        def __exit__(self, *exc):
            return False

    def factory():
        return Factory()

    factory.holder = holder
    return factory, holder


CHAT_LIST = '[aria-label="Search or start a new chat"]'
SEND_ICON = '[data-testid="wds-ic-send-filled"]'
SEND_ICON_VISIBLE = '[data-testid="wds-ic-send-filled"]:visible'
PHONE_INPUT = '[data-testid="phone-number-input"], input[autocomplete="tel"], input[type="tel"]'
LOGIN_LINK = 'text=/Link with phone number instead|Log in with phone number/'
NEXT_BUTTON = 'button:has-text("Next")'


def login_page(body_text='', country_options=(), phone_rejects=()):
    """A page sitting on the WhatsApp phone-number login screen."""
    page = FakePage(
        body_text=body_text,
        country_options=country_options,
        phone_rejects=phone_rejects,
    )
    page.show(LOGIN_LINK)
    page.show(PHONE_INPUT)
    page.show(NEXT_BUTTON)
    return page


# NOTE: logged_in_page() is defined once, further down, next to the send
# orchestration tests that use it. It used to be defined here too, without
# SEND_ICON_VISIBLE — so the copy down below silently shadowed it. Nothing
# caught it because the shadowed version was unreachable, not wrong.


# --- Pacing ---------------------------------------------------------------


class PacingTests(SimpleTestCase):
    def test_long_break_lands_inside_the_configured_band(self):
        for start in (0, 20, 100):
            for _ in range(200):
                target = wa._next_long_break_at(start)
                self.assertGreaterEqual(target, start + wa.LONG_BREAK_MIN_INTERVAL)
                self.assertLessEqual(target, start + wa.LONG_BREAK_MAX_INTERVAL)

    def test_long_break_interval_is_not_fixed(self):
        """
        A constant interval would itself be a pattern. Verified over many
        draws rather than two, so a lucky constant implementation fails.
        """
        targets = {wa._next_long_break_at(0) for _ in range(200)}
        self.assertGreater(len(targets), 1)

    @mock.patch('sender.whatsapp_automation.time.sleep')
    def test_instant_mode_never_long_breaks(self, sleep):
        target = wa._next_long_break_at()
        for i in range(60):
            returned = wa._sleep_between_messages(wa.INSTANT_DELAY_SECONDS, i + 1, target)
        self.assertEqual(target, returned, 'instant mode must not re-roll the break target')
        for call in sleep.call_args_list:
            self.assertEqual(call.args[0], wa.INSTANT_DELAY_SECONDS)

    @mock.patch('sender.whatsapp_automation.time.sleep')
    @mock.patch('sender.whatsapp_automation.random.uniform')
    def test_delay_mode_uses_the_wide_band(self, uniform, sleep):
        uniform.return_value = 7.0
        target = wa._next_long_break_at()
        wa._sleep_between_messages(20, 5, target)
        uniform.assert_called_once_with(wa.DELAY_MIN_SECONDS, wa.DELAY_MAX_SECONDS)
        sleep.assert_called_once_with(7.0)

    @mock.patch('sender.whatsapp_automation.time.sleep')
    @mock.patch('sender.whatsapp_automation.random.uniform')
    def test_delay_mode_long_break_fires_and_re_rolls(self, uniform, sleep):
        uniform.return_value = 99.0
        target = 10
        returned = wa._sleep_between_messages(20, 12, target)
        uniform.assert_called_once_with(wa.LONG_BREAK_MIN_SECONDS, wa.LONG_BREAK_MAX_SECONDS)
        self.assertGreater(returned, 12, 'next break must be re-rolled past the current count')


class LocalDigitsTests(SimpleTestCase):
    def test_strips_the_calling_code(self):
        self.assertEqual(wa._local_digits_for('+2348161234765', '234'), '8161234765')
        self.assertEqual(wa._local_digits_for('+14155552671', '1'), '4155552671')

    def test_leaves_the_number_alone_when_prefix_differs(self):
        self.assertEqual(wa._local_digits_for('+447911123456', '234'), '447911123456')

    def test_handles_empty_and_missing_code(self):
        self.assertEqual(wa._local_digits_for('+2348161234765', ''), '2348161234765')
        self.assertEqual(wa._local_digits_for('', '234'), '')


# --- Phone entry ----------------------------------------------------------


class PhoneEntryTests(SimpleTestCase):
    def test_selects_country_and_types_national_digits(self):
        page = login_page(country_options={'Nigeria'})
        wa._fill_phone_login(page, '+2348161234765')

        self.assertTrue(
            page.locator('[role="option"]:has-text("Nigeria"), li:has-text("Nigeria"), div:has-text("Nigeria")').clicked
        )
        self.assertEqual(page.current_input, '8161234765')

    def test_falls_back_to_international_form_when_rejected(self):
        """
        The country dropdown may not contain a matching name (the name comes
        from a range-based lookup that can disagree with the region). If
        WhatsApp rejects the national form, retry with the full number.
        """
        page = login_page(country_options={'Guernsey'}, phone_rejects={'7911123456'})
        wa._fill_phone_login(page, '+447911123456')
        self.assertEqual(page.current_input, '+447911123456')

    def test_raises_when_both_forms_are_rejected(self):
        page = login_page(
            country_options={'Guernsey'},
            phone_rejects={'7911123456', '+447911123456'},
        )
        with self.assertRaises(ValueError) as ctx:
            wa._fill_phone_login(page, '+447911123456')
        self.assertIn('rejected', str(ctx.exception))
        self.assertIn('WhatsApp', str(ctx.exception))

    def test_empty_number_raises(self):
        with self.assertRaises(ValueError):
            wa._fill_phone_login(login_page(), '')


class CountrySelectionTests(SimpleTestCase):
    def test_reports_failure_when_option_absent(self):
        self.assertFalse(wa._select_country(FakePage(), 'Nigeria'))

    def test_matches_a_prefix_of_a_longer_geocoder_name(self):
        # geocoder can return "Nigeria" or "Nigeria, Lagos"; the code splits
        # on the comma and tries the bare country name too.
        page = FakePage(country_options={'Nigeria'})
        self.assertTrue(wa._select_country(page, 'Nigeria, Lagos'))

    def test_rejection_detection_reflects_error_element_visibility(self):
        page = FakePage()
        self.assertFalse(wa._phone_field_rejected(page, PHONE_INPUT))

        page.show('[data-testid="phone-number-input-error"]')
        self.assertTrue(wa._phone_field_rejected(page, PHONE_INPUT))

        page.locators['[data-testid="phone-number-input-error"]'].visible = False
        self.assertFalse(wa._phone_field_rejected(page, PHONE_INPUT))


# --- Login code scraping --------------------------------------------------


class LoginCodeTests(SimpleTestCase):
    def test_scrapes_a_dashed_code_from_page_text(self):
        page = login_page(body_text='Enter code on phone\nABCD-1234\nOther text')
        codes = []
        wa._request_login_code(page, '+2348161234765', codes.append, lambda e: None)
        self.assertEqual(codes, ['ABCD1234'])

    def test_handles_space_separated_code(self):
        page = login_page(body_text='Enter code on phone\nABCD 1234')
        codes = []
        wa._request_login_code(page, '+2348161234765', codes.append, lambda e: None)
        self.assertEqual(codes, ['ABCD1234'])

    def test_reports_error_when_no_code_appears(self):
        page = login_page(body_text='Enter your phone number')
        errors = []
        with mock.patch.object(wa, 'LOGIN_CODE_POLL_TIMEOUT_SECONDS', 0.01):
            wa._request_login_code(page, '+2348161234765', lambda c: None, errors.append)
        self.assertTrue(errors)
        self.assertIn('never displayed a login code', errors[0])

    def test_reports_error_when_login_link_never_appears(self):
        """WhatsApp's markup changed, or the page is still spinning."""
        page = login_page()
        page.hide(LOGIN_LINK)
        errors = []
        wa._request_login_code(page, '+2348161234765', lambda c: None, errors.append)
        self.assertTrue(errors)
        self.assertIn('Could not start WhatsApp login', errors[0])

    def test_screenshots_are_opt_in(self):
        """
        These used to be written unconditionally into MEDIA_ROOT, which was
        served publicly — a screenshot of a logged-in WhatsApp session.
        """
        with mock.patch.dict('os.environ', {'WHATSAPP_DEBUG': 'false'}):
            page = login_page(body_text='nothing useful')
            with mock.patch.object(wa, 'LOGIN_CODE_POLL_TIMEOUT_SECONDS', 0.01):
                wa._request_login_code(page, '+2348161234765', lambda c: None, lambda e: None)
            self.assertEqual(page.screenshots, [])

        with mock.patch.dict('os.environ', {'WHATSAPP_DEBUG': 'true'}):
            page2 = login_page(body_text='nothing useful')
            with mock.patch.object(wa, 'LOGIN_CODE_POLL_TIMEOUT_SECONDS', 0.01):
                wa._request_login_code(page2, '+2348161234765', lambda c: None, lambda e: None)
            self.assertTrue(page2.screenshots)
            self.assertTrue(
                all('media' not in str(s).lower() for s in page2.screenshots),
                'screenshots must never land in MEDIA_ROOT',
            )


class LoginWaitTests(SimpleTestCase):
    @mock.patch('sender.whatsapp_automation.LOGIN_SLOW_WARNING_MS', 10)
    @mock.patch('sender.whatsapp_automation.LOGIN_TOTAL_TIMEOUT_MS', 20)
    def test_returns_true_immediately_when_chat_list_present(self):
        slow, failed = [], []
        self.assertTrue(
            wa._wait_for_login(logged_in_page(), lambda: slow.append(1), lambda: failed.append(1))
        )
        self.assertFalse(slow)
        self.assertFalse(failed)

    @mock.patch('sender.whatsapp_automation.LOGIN_SLOW_WARNING_MS', 10)
    @mock.patch('sender.whatsapp_automation.LOGIN_TOTAL_TIMEOUT_MS', 20)
    def test_flags_slow_then_succeeds(self):
        calls = {'n': 0}

        class SlowPage(FakePage):
            def wait_for_selector(self, selector, timeout=None):
                calls['n'] += 1
                if calls['n'] == 1:
                    raise AssertionError('timeout')
                # succeeds on the second stage

        slow = []
        self.assertTrue(wa._wait_for_login(SlowPage(), lambda: slow.append(1), lambda: None))
        self.assertEqual(slow, [1])

    @mock.patch('sender.whatsapp_automation.LOGIN_SLOW_WARNING_MS', 10)
    @mock.patch('sender.whatsapp_automation.LOGIN_TOTAL_TIMEOUT_MS', 20)
    def test_flags_failed_when_login_never_happens(self):
        class NeverPage(FakePage):
            def wait_for_selector(self, selector, timeout=None):
                raise AssertionError('timeout')

        slow, failed = [], []
        self.assertFalse(wa._wait_for_login(NeverPage(), lambda: slow.append(1), lambda: failed.append(1)))
        self.assertEqual(slow, [1])
        self.assertEqual(failed, [1])


# --- Send orchestration ---------------------------------------------------


def logged_in_page():
    """A page already past the handshake, with a working send button."""
    page = FakePage(body_text='chats')
    page.show(CHAT_LIST)
    page.show(SEND_ICON)
    page.show(SEND_ICON_VISIBLE)
    return page


class DyingBrowserPage(FakePage):
    """
    A logged-in page whose browser process dies after N successful sends.

    Models the real failure: an OOM kill or a killed tab. Every later send
    fails because the page is gone, and `title()` — the liveness probe the
    send loop uses — raises, exactly as Playwright does for a closed target.

    `send_attempts` counts how many contacts were actually attempted, which is
    what distinguishes stopping early from grinding through the remainder.
    """

    def __init__(self, die_after=1):
        super().__init__(body_text='chats')
        self.show(CHAT_LIST)
        self.show(SEND_ICON)
        self.show(SEND_ICON_VISIBLE)
        self.die_after = die_after
        self.send_attempts = 0

    def goto(self, url, timeout=None):
        if '/send?phone=' in url:
            self.send_attempts += 1
            if self.send_attempts > self.die_after:
                self.dead = True
                from playwright.sync_api import Error as PlaywrightError

                raise PlaywrightError('Target page, context or browser has been closed')
        return super().goto(url, timeout=timeout)


class SendOrchestrationTests(SimpleTestCase):
    """
    Runs the real send_whatsapp_messages entry point against a fake browser.

    Session directories are redirected to a temp path so these tests don't
    create user_N/ directories inside the repo, and so the retry path's
    rmtree/mkdir cycle can't touch a real profile.
    """

    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        self._sessions = Path(self._tmp.name)
        self._patcher = mock.patch.object(wa, 'SESSIONS_DIR', self._sessions)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def _run(self, page, **kwargs):
        factory, holder = fake_playwright(page)
        options = {
            'contacts': [{'id': 1, 'name': 'Ada', 'phone': '+2348161234765'}],
            'message_text': 'Hi {name}',
            'user_id': 1,
            'whatsapp_number': '+2348161234765',
            'delay_seconds': wa.INSTANT_DELAY_SECONDS,
        }
        options.update(kwargs)
        with mock.patch.dict('os.environ', {'WHATSAPP_HEADLESS': 'true'}), \
             mock.patch.object(wa, 'LOGIN_CODE_POLL_TIMEOUT_SECONDS', 0.01), \
             mock.patch('sender.whatsapp_automation.time.sleep') as sleep:
            result = wa.send_whatsapp_messages(playwright_factory=factory, **options)
        self.assert_slept = sleep
        return result, holder

    def test_empty_contact_list_short_circuits(self):
        result = wa.send_whatsapp_messages(
            contacts=[],
            message_text='hi',
            user_id=1,
            whatsapp_number='+2348161234765',
        )
        self.assertEqual(result, {'sent': 0, 'failed': 0, 'logged_in': True})

    def test_uses_existing_session_when_already_logged_in(self):
        """
        The old code deleted the profile at the start of every send, forcing
        a fresh login-code round trip for every single campaign.
        """
        result, holder = self._run(logged_in_page())
        self.assertTrue(result['logged_in'])
        self.assertEqual(len(holder.contexts), 1, 'exactly one browser session')
        chat_urls = [u for u in holder.contexts[0].page.goto_urls if '/send?phone=' in u]
        self.assertEqual(len(chat_urls), 1)
        self.assertEqual(result['sent'], 1)

    def test_personalizes_name_placeholder(self):
        page = logged_in_page()
        self._run(
            page,
            contacts=[{'id': 1, 'name': 'Ada Lovelace', 'phone': '+2348161234765'}],
            message_text='Hi {name}, welcome',
        )
        # goto_urls[0] is the initial navigation to web.whatsapp.com.
        chat_urls = [u for u in page.goto_urls if '/send?phone=' in u]
        self.assertEqual(len(chat_urls), 1)
        self.assertIn('Hi%20Ada%20Lovelace%2C%20welcome', chat_urls[0])
        self.assertIn('phone=2348161234765', chat_urls[0])

    def test_handles_missing_name_placeholder(self):
        page = logged_in_page()
        result, _ = self._run(page, contacts=[{'id': 1, 'name': '', 'phone': '+2348161234765'}])
        self.assertEqual(result['sent'], 1)

    def test_contact_with_no_digits_is_marked_failed(self):
        statuses = []
        result, _ = self._run(
            logged_in_page(),
            contacts=[{'id': 7, 'name': 'Broken', 'phone': '+++'}],
            on_progress=lambda cid, st: statuses.append((cid, st)),
        )
        self.assertEqual(statuses, [(7, 'failed')])
        self.assertEqual(result['failed'], 1)

    def test_runaway_login_triggers_exactly_one_retry(self):
        """
        A session that never links gets one retry against a wiped profile,
        then gives up. The retry count is asserted because an unbounded loop
        would leave a browser running forever.
        """
        attempts = []

        class Never(FakePage):
            def wait_for_selector(self, selector, timeout=None):
                if selector == CHAT_LIST:
                    raise AssertionError('never logged in')
                return super().wait_for_selector(selector, timeout)

        def factory():
            attempts.append(1)

            class F:
                def __enter__(self):
                    return FakePlaywright(Never(body_text='Enter your phone number'))
                def __exit__(self, *a):
                    return False
            return F()

        with mock.patch.dict('os.environ', {'WHATSAPP_HEADLESS': 'true'}), \
             mock.patch.object(wa, 'LOGIN_SLOW_WARNING_MS', 5), \
             mock.patch.object(wa, 'LOGIN_TOTAL_TIMEOUT_MS', 10), \
             mock.patch.object(wa, 'LOGIN_CODE_POLL_TIMEOUT_SECONDS', 0.01):
            result = wa.send_whatsapp_messages(
                contacts=[{'id': 1, 'name': 'Ada', 'phone': '+2348161234765'}],
                message_text='hi',
                user_id=97,
                whatsapp_number='+2348161234765',
                delay_seconds=wa.INSTANT_DELAY_SECONDS,
                playwright_factory=factory,
            )

        self.assertFalse(result['logged_in'])
        self.assertEqual(len(attempts), 1 + wa.SESSION_RETRIES)

    def test_pending_contacts_are_settled_on_login_failure(self):
        """
        Contacts that never got a turn used to stay 'pending' forever, which
        made the frontend progress bar spin indefinitely.
        """
        class Never(FakePage):
            def wait_for_selector(self, selector, timeout=None):
                if selector == CHAT_LIST:
                    raise AssertionError('never')
                return super().wait_for_selector(selector, timeout)

        statuses = []

        def factory():
            class F:
                def __enter__(self):
                    return FakePlaywright(Never(body_text='Enter your phone number'))
                def __exit__(self, *a):
                    return False
            return F()

        with mock.patch.dict('os.environ', {'WHATSAPP_HEADLESS': 'true'}), \
             mock.patch.object(wa, 'LOGIN_SLOW_WARNING_MS', 5), \
             mock.patch.object(wa, 'LOGIN_TOTAL_TIMEOUT_MS', 10):
            wa.send_whatsapp_messages(
                contacts=[{'id': 1, 'name': 'A', 'phone': '+2348161234765'}],
                message_text='hi',
                user_id=96,
                whatsapp_number='+2348161234765',
                delay_seconds=wa.INSTANT_DELAY_SECONDS,
                playwright_factory=factory,
                on_progress=lambda cid, st: statuses.append(st),
            )

        self.assertIn('failed', statuses)
        self.assertNotIn('pending', statuses)

    def test_login_failure_is_reported_through_the_callback(self):
        class Never(FakePage):
            def wait_for_selector(self, selector, timeout=None):
                if selector == CHAT_LIST:
                    raise AssertionError('never')
                return super().wait_for_selector(selector, timeout)

        failures = []

        def factory():
            class F:
                def __enter__(self):
                    return FakePlaywright(Never(body_text='Enter your phone number'))
                def __exit__(self, *a):
                    return False
            return F()

        with mock.patch.dict('os.environ', {'WHATSAPP_HEADLESS': 'true'}), \
             mock.patch.object(wa, 'LOGIN_SLOW_WARNING_MS', 5), \
             mock.patch.object(wa, 'LOGIN_TOTAL_TIMEOUT_MS', 10):
            wa.send_whatsapp_messages(
                contacts=[{'id': 1, 'name': 'A', 'phone': '+2348161234765'}],
                message_text='hi',
                user_id=95,
                whatsapp_number='+2348161234765',
                delay_seconds=wa.INSTANT_DELAY_SECONDS,
                playwright_factory=factory,
                on_login_failed=lambda: failures.append(1),
            )

        self.assertTrue(failures)

    def test_browser_is_always_closed(self):
        _, holder = self._run(logged_in_page())
        self.assertTrue(holder.contexts[0].closed)

    def test_slow_login_is_flagged_then_recovers(self):
        calls = {'n': 0}

        class SlowThenReady(FakePage):
            def wait_for_selector(self, selector, timeout=None):
                if selector == CHAT_LIST:
                    calls['n'] += 1
                    if calls['n'] == 1:
                        raise AssertionError('slow')
                return super().wait_for_selector(selector, timeout)

        page = SlowThenReady(body_text='chats')
        page.show(CHAT_LIST)
        page.show(SEND_ICON)
        page.show(SEND_ICON_VISIBLE)

        slow = []
        statuses = []
        with mock.patch.dict('os.environ', {'WHATSAPP_HEADLESS': 'true'}), \
             mock.patch.object(wa, 'LOGIN_SLOW_WARNING_MS', 5), \
             mock.patch.object(wa, 'LOGIN_TOTAL_TIMEOUT_MS', 50), \
             mock.patch.object(wa, 'LOGIN_CODE_POLL_TIMEOUT_SECONDS', 0.01):
            result, _ = self._run(
                page,
                on_login_slow=lambda: slow.append(1),
                on_progress=lambda cid, st: statuses.append(st),
            )

        self.assertTrue(slow)
        self.assertTrue(result['logged_in'])
        self.assertEqual(statuses, ['sent'])


class RetrySafetyTests(SimpleTestCase):
    """
    Guards against duplicate delivery on retry.

    A browser crash part-way through returns logged_in=False together with a
    non-zero 'sent' count. The retry loop used to re-run the full contact list
    in that case, delivering a second message to everyone already reached.
    """

    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(wa, 'SESSIONS_DIR', Path(self._tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def run_with_results(self, results):
        """Drives send_whatsapp_messages with a stubbed _one_pass."""
        with mock.patch.object(wa, '_one_pass', side_effect=results) as one_pass:
            result = wa.send_whatsapp_messages(
                contacts=[
                    {'id': 1, 'name': 'A', 'phone': '+2348161234765'},
                    {'id': 2, 'name': 'B', 'phone': '+2348161234766'},
                ],
                message_text='hi',
                user_id=42,
                whatsapp_number='+2348161234765',
                delay_seconds=wa.INSTANT_DELAY_SECONDS,
                playwright_factory=mock.Mock(),
            )
        return result, one_pass

    def test_does_not_retry_after_a_partial_send(self):
        """The critical case: 2 sent, then a crash. No second attempt."""
        result, one_pass = self.run_with_results([
            {'sent': 2, 'failed': 0, 'logged_in': False},
        ])

        self.assertEqual(one_pass.call_count, 1, 'must not re-run the send')
        self.assertEqual(result['sent'], 2)

    def test_does_not_retry_after_a_partial_send_with_failures(self):
        result, one_pass = self.run_with_results([
            {'sent': 5, 'failed': 3, 'logged_in': False},
        ])

        self.assertEqual(one_pass.call_count, 1)
        self.assertEqual(result['sent'], 5)

    def test_does_retry_after_a_pure_login_failure(self):
        """Nothing was sent, so re-linking is safe and worth doing."""
        result, one_pass = self.run_with_results([
            {'sent': 0, 'failed': 0, 'logged_in': False},
            {'sent': 2, 'failed': 0, 'logged_in': True},
        ])

        self.assertEqual(one_pass.call_count, 2)
        self.assertTrue(result['logged_in'])
        self.assertEqual(result['sent'], 2)

    def test_retry_count_is_bounded(self):
        result, one_pass = self.run_with_results([
            {'sent': 0, 'failed': 0, 'logged_in': False},
        ] * (wa.SESSION_RETRIES + 3))

        self.assertEqual(one_pass.call_count, 1 + wa.SESSION_RETRIES)

    def test_successful_first_pass_is_not_retried(self):
        _, one_pass = self.run_with_results([
            {'sent': 2, 'failed': 0, 'logged_in': True},
        ])

        self.assertEqual(one_pass.call_count, 1)


class CrashSettlementTests(SimpleTestCase):
    """
    The crash handler inside _one_pass must not unsettle delivered contacts.

    This is the second route to duplicate delivery, and it is independent of
    the retry loop guarded by RetrySafetyTests: if a browser dies part-way
    through, the handler settles "everything that hasn't been sent" as failed.
    Getting that filter wrong overwrites rows that were already delivered, so
    retry_campaign hands them back as pending and the next run messages people
    a second time.

    Reachability, stated honestly: _send_to_contact catches every exception
    itself, so a PlaywrightError raised *inside* the per-contact send is
    swallowed and never reaches this handler (verified -- the run finishes with
    logged_in=True). The handler is therefore only reachable today from the
    browser calls that happen outside that try, or from anything else raising
    in the loop body. That makes the bug latent rather than live, but the
    filter is still load-bearing for correctness the moment it is reached, so
    it is pinned here rather than left to rot.
    """

    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(wa, 'SESSIONS_DIR', Path(self._tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def run_with_crash(self):
        """
        Sends three contacts, crashing after the second is delivered.

        The crash is injected at _sleep_between_messages, the one point in the
        loop body outside _send_to_contact's own try/except, which is what
        makes the handler reachable from a test at all. The pacing call runs
        *after* each contact is handled, so raising on the second call leaves
        contacts 1 and 2 delivered and contact 3 untouched.
        """
        from playwright.sync_api import Error as PlaywrightError

        statuses = []
        sleeps = {'n': 0}

        def crash_after_second_contact(*args, **kwargs):
            sleeps['n'] += 1
            if sleeps['n'] >= 2:
                raise PlaywrightError('browser died between messages')
            return None

        factory, holder = fake_playwright(logged_in_page())

        with mock.patch.dict('os.environ', {'WHATSAPP_HEADLESS': 'true'}), \
             mock.patch.object(
                 wa, '_sleep_between_messages', side_effect=crash_after_second_contact
             ):
            result = wa.send_whatsapp_messages(
                contacts=[
                    {'id': 1, 'name': 'A', 'phone': '+2348161234765'},
                    {'id': 2, 'name': 'B', 'phone': '+2348161234766'},
                    {'id': 3, 'name': 'C', 'phone': '+2348161234767'},
                ],
                message_text='hi',
                user_id=43,
                whatsapp_number='+2348161234765',
                delay_seconds=wa.INSTANT_DELAY_SECONDS,
                playwright_factory=factory,
                on_progress=lambda cid, status: statuses.append((cid, status)),
            )
        return result, statuses

    def test_delivered_contacts_are_not_rewritten_to_failed(self):
        _, statuses = self.run_with_crash()

        self.assertEqual(
            [s for s in statuses if s[0] in (1, 2)],
            [(1, 'sent'), (2, 'sent')],
            'contacts 1 and 2 were delivered and must be left alone',
        )
        for contact_id in (1, 2):
            self.assertNotIn(
                (contact_id, 'failed'), statuses,
                'the crash handler re-settled an already-delivered contact, '
                'which would let retry_campaign re-send it',
            )

    def test_undelivered_contact_is_settled_as_failed(self):
        _, statuses = self.run_with_crash()

        self.assertIn((3, 'failed'), statuses)

    def test_reports_the_true_sent_count(self):
        result, _ = self.run_with_crash()

        self.assertEqual(result['sent'], 2)
        self.assertEqual(result['failed'], 1)
        self.assertFalse(result['logged_in'])

    def test_filter_cannot_depend_on_a_status_key_that_does_not_exist(self):
        """
        Pins the reason the old filter was wrong, independent of reachability.

        Contacts reach this module as {'id', 'name', 'phone'} — see
        views.run_campaign_send, which builds them with
        .values('id', 'name', 'phone'). There is no 'status' key, so a filter
        written against one silently keeps everything.
        """
        contacts = [{'id': 1, 'name': 'A', 'phone': '+2348161234765'}]
        self.assertNotIn('status', contacts[0])

        legacy_filter = [c for c in contacts if c.get('status') != 'sent']
        self.assertEqual(
            legacy_filter, contacts,
            'the legacy filter is a no-op, which is why sent_ids replaced it',
        )


class DeadBrowserTests(SimpleTestCase):
    """
    A browser that dies mid-campaign must stop the send, not limp on.

    Two things went wrong here. The loop carried on after the browser died,
    burning the full per-contact timeout (goto 120s + wait_for_selector 90s)
    once for every remaining contact — on a 500-contact list, most of a day
    holding a worker thread — and because on_progress still fired, the
    heartbeat stayed fresh so the stale-campaign reaper never intervened.
    And the run returned logged_in=True, so the campaign was recorded as
    COMPLETED rather than failed.
    """

    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(wa, 'SESSIONS_DIR', Path(self._tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def run_dying(self, die_after=1, contact_count=5):
        page = DyingBrowserPage(die_after=die_after)
        statuses = []
        factory, holder = fake_playwright(page)

        with mock.patch.dict('os.environ', {'WHATSAPP_HEADLESS': 'true'}), \
             mock.patch('sender.whatsapp_automation.time.sleep'):
            result = wa.send_whatsapp_messages(
                contacts=[
                    {'id': i, 'name': f'C{i}', 'phone': f'+234816123476{i}'}
                    for i in range(1, contact_count + 1)
                ],
                message_text='hi',
                user_id=55,
                whatsapp_number='+2348161234765',
                delay_seconds=wa.INSTANT_DELAY_SECONDS,
                playwright_factory=factory,
                on_progress=lambda cid, status: statuses.append((cid, status)),
            )
        return result, statuses, page, holder

    def test_stops_instead_of_failing_every_remaining_contact(self):
        """
        The core assertion. One send succeeds, the browser dies, and only the
        contact that triggered the death is attempted — the other three are
        never touched. Before the fix all five were attempted, each paying the
        full timeout against a dead browser.
        """
        _, _, page, _ = self.run_dying(die_after=1, contact_count=5)

        self.assertEqual(
            page.send_attempts, 2,
            'expected the browser to be abandoned after the failed send, not '
            'worked through the rest of the list',
        )

    def test_campaign_is_not_reported_as_logged_in(self):
        """
        logged_in=True made views mark the campaign COMPLETED, which told the
        user a dead browser had finished the job.
        """
        result, _, _, _ = self.run_dying()

        self.assertFalse(result['logged_in'])

    def test_delivered_contact_stays_sent_and_the_rest_are_settled(self):
        _, statuses, _, _ = self.run_dying(die_after=1, contact_count=5)

        self.assertIn((1, 'sent'), statuses)
        self.assertNotIn(
            (1, 'failed'), statuses,
            'a delivered contact must never be rewritten, or retry_campaign '
            'would send it a second time',
        )
        for contact_id in (2, 3, 4, 5):
            self.assertIn((contact_id, 'failed'), statuses)

    def test_reports_true_counts(self):
        result, _, _, _ = self.run_dying(die_after=1, contact_count=5)

        self.assertEqual(result['sent'], 1)
        self.assertEqual(result['failed'], 4)

    def test_never_retries_after_delivering_something(self):
        """
        The property that makes aborting safe: a dead browser after a partial
        send must not trigger the re-link-and-resend path.
        """
        with mock.patch.object(wa, '_one_pass', wraps=wa._one_pass) as one_pass:
            self.run_dying(die_after=1, contact_count=5)

        self.assertEqual(
            one_pass.call_count, 1,
            'a partially delivered campaign must not be re-sent',
        )

    def test_live_browser_failures_do_not_stop_the_run(self):
        """
        The abort must key on a dead browser, not on a failed send. A single
        undeliverable message is routine and the run should carry on.
        """
        result, statuses, page, _ = self.run_dying(die_after=99, contact_count=3)

        self.assertEqual(page.send_attempts, 3, 'every contact should be attempted')
        self.assertTrue(result['logged_in'])
        self.assertEqual(result['sent'], 3)
        self.assertNotIn('failed', [s for _, s in statuses])


class SessionDirTests(SimpleTestCase):
    def test_sessions_are_isolated_per_user(self):
        self.assertNotEqual(wa.get_session_dir(1), wa.get_session_dir(2))
        self.assertIn('user_1', str(wa.get_session_dir(1)))

    @mock.patch('sender.whatsapp_automation.shutil.rmtree')
    def test_reset_creates_the_directory(self, rmtree):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(wa, 'SESSIONS_DIR', Path(tmp)):
                wa.reset_session(5)
                self.assertTrue((Path(tmp) / 'user_5').exists())


class BrowserSelectionTests(SimpleTestCase):
    def test_env_override_wins(self):
        import sys

        # The override is only honoured if the path exists, so point it at
        # something guaranteed to be there.
        with mock.patch.dict('os.environ', {'WHATSAPP_BROWSER_PATH': sys.executable}):
            self.assertEqual(wa._find_installed_browser(), sys.executable)

    def test_missing_env_override_is_skipped(self):
        with mock.patch.dict('os.environ', {'WHATSAPP_BROWSER_PATH': '/nope/chrome'}):
            found = wa._find_installed_browser()
            self.assertNotEqual(found, '/nope/chrome')

    def test_channel_mapping(self):
        self.assertEqual(wa._browser_channel_for(r'C:\msedge.exe'), 'msedge')
        self.assertEqual(wa._browser_channel_for('/usr/bin/google-chrome'), 'chrome')
        # Playwright has no 'brave' channel; 'chrome' is the correct
        # chromium-compatible stand-in (and unused when we pass an
        # executable_path anyway).
        self.assertEqual(wa._browser_channel_for('/opt/brave'), 'chrome')
        self.assertEqual(wa._browser_channel_for('/usr/bin/firefox'), 'chromium')
        self.assertEqual(wa._browser_channel_for(None), 'chromium')