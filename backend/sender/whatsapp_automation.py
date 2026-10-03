"""
Playwright-driven WhatsApp Web automation.

This is the fragile core of the app. WhatsApp Web has no public send API,
so a real browser session is driven through the linked-device handshake and
then asked to open a chat URL and hit send, once per contact.

Everything here is a best-effort integration against a third-party UI that
changes without notice. The design decisions worth knowing about:

* Sessions persist per user (SESSIONS_DIR/user_<id>/) so people are not
  forced to re-link WhatsApp on every campaign. The old code deleted the
  whole profile at the start of every send, which meant a fresh login code
  round-trip for each campaign — several minutes of the user staring at a
  code prompt for every single send.

* Login state is reported to the database, not to files in MEDIA_ROOT.
  gunicorn runs multiple workers, and PaaS filesystems are ephemeral, so
  file-based IPC was unreliable in exactly the deployment the README
  describes. See models.WhatsAppLoginState.

* A session that looks logged out gets ONE automatic retry after wiping
  the profile, because that is the single most common failure mode after a
  WhatsApp-side session expiry and it used to require the user to start
  the whole campaign over.

* Anti-detection (header spoofing, navigator.webdriver masking, humanized
  pacing) reduces trivial bot signals. It is NOT a guarantee. WhatsApp can
  and does ban accounts for bulk messaging regardless, and no amount of
  delay tuning changes that. Users should know this before they point it at
  a number they care about.
"""

import logging
import os
import random
import re
import shutil
import time
import urllib.parse
from pathlib import Path

from .phone_numbers import NON_DIGITS, country_for

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
SESSIONS_DIR = Path(os.environ.get('WHATSAPP_SESSION_DIR', BASE_DIR / 'whatsapp_session'))

# Where debug screenshots go. Deliberately NOT MEDIA_ROOT — these capture a
# logged-in WhatsApp session and must never land somewhere web-servable.
DEBUG_DIR = Path(os.environ.get('WHATSAPP_DEBUG_DIR', BASE_DIR / 'whatsapp_debug'))

# Ceilings, not fixed waits: wait_for_selector returns the instant the
# selector appears, so a healthy login finishes in seconds. These only
# matter when someone is actually stuck.
LOGIN_SLOW_WARNING_MS = 300_000   # 5 min — flag "slow network" in the UI
LOGIN_TOTAL_TIMEOUT_MS = 600_000  # 10 min — hard ceiling before giving up


# --- Humanized pacing ---------------------------------------------------
# A fixed gap between every message is itself a bot signal. Delay mode
# widens the band and adds an occasional longer pause, re-rolled each time
# so the long-break interval isn't fixed either.
DELAY_MIN_SECONDS = 5
DELAY_MAX_SECONDS = 15
LONG_BREAK_MIN_INTERVAL = 20   # messages
LONG_BREAK_MAX_INTERVAL = 30   # messages
LONG_BREAK_MIN_SECONDS = 45
LONG_BREAK_MAX_SECONDS = 120

INSTANT_DELAY_SECONDS = 1

# --- Selectors -----------------------------------------------------------
# Named constants rather than inline strings, so the preflight management
# command and the send path can't drift apart, and so there's one place to
# update when WhatsApp changes their markup.

PHONE_LOGIN_LINK_SELECTOR = 'text=/Link with phone number instead|Log in with phone number/'
PHONE_INPUT_SELECTOR = '[data-testid="phone-number-input"], input[autocomplete="tel"], input[type="tel"]'
NEXT_BUTTON_SELECTOR = 'button:has-text("Next")'
CHAT_LIST_SELECTOR = '[aria-label="Search or start a new chat"]'
SEND_ICON_SELECTOR = '[data-testid="wds-ic-send-filled"]'
ATTACH_BUTTON_SELECTOR = '[data-testid="plus-rounded"]'
ATTACH_MENU_ITEM_SELECTOR = 'text=Photos & videos'

# The code renders as separate characters, optionally split around a dash.
# The separator is optional between any two characters so all observed layouts
# match: ABCD-1234, ABCD 1234, ABCD1234.
LOGIN_CODE_PATTERN = re.compile(
    r'Enter code on phone.*?(?<![A-Z0-9])([A-Z0-9](?:[\s\-]?[A-Z0-9]){7})(?![A-Z0-9])',
    flags=re.S | re.I,
)

# Elements WhatsApp shows when it will not accept the number for the selected
# country. Used to detect a bad country selection and retry in international
# form rather than clicking Next and waiting out the login timeout.
PHONE_REJECTION_SELECTORS = (
    '[data-testid="phone-number-input-error"]',
    'div[role="alert"]',
    'span[data-testid="error-message"]',
)

# How long to keep re-reading the page looking for the login code before
# giving up. The code normally appears in one or two polls; this is a ceiling
# for the case where WhatsApp renders the screen but stays slow.
LOGIN_CODE_POLL_TIMEOUT_SECONDS = float(
    os.environ.get('WHATSAPP_LOGIN_CODE_TIMEOUT', '30')
)

# How many times to retry a send after wiping a stale session and getting a
# fresh login prompt. One retry: if it fails again, the problem is not the
# session and retrying just burns another 10 minutes of the user's time.
SESSION_RETRIES = 1


def _env_flag(name, default='false'):
    return os.environ.get(name, default).strip().lower() in {'1', 'true', 'yes', 'on'}


def _find_installed_browser():
    """
    Prefer a real local Chrome/Edge/Brave over Playwright's bundled Chromium.

    WhatsApp Web is strict about the browser it accepts for the linked-device
    flow, and a manually-installed Chrome/Edge is markedly more reliable than
    the bundled build — especially on a machine where the user will finish the
    phone-side handshake.
    """
    candidates = []
    for env_key in ('WHATSAPP_BROWSER_PATH', 'CHROME_PATH', 'EDGE_PATH'):
        value = os.environ.get(env_key)
        if value:
            candidates.append(value)

    candidates.extend([
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files\Google\Chrome for Testing\Application\chrome.exe',
        r'C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe',
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
        '/usr/bin/google-chrome',
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
    ])

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def _browser_channel_for(executable_path):
    if not executable_path:
        return os.environ.get('WHATSAPP_BROWSER_CHANNEL', '').strip().lower() or 'chromium'

    lower = executable_path.lower()
    if 'msedge' in lower:
        return 'msedge'
    if 'chrome' in lower or 'brave' in lower:
        return 'chrome'
    return 'chromium'


def get_session_dir(user_id):
    """
    Per-user persistent browser profile.

    Isolating by user is what makes two people sending at the same time
    possible at all: two Chromium processes sharing one user-data-dir fight
    over its storage and corrupt the session.
    """
    return SESSIONS_DIR / f'user_{user_id}'


def reset_session(user_id):
    """Wipes a user's WhatsApp profile, forcing a fresh link next time."""
    session_dir = get_session_dir(user_id)
    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)
        logger.info('Reset WhatsApp session for user %s', user_id)
    session_dir.mkdir(parents=True, exist_ok=True)


def _launch_browser_context(playwright, session_dir):
    browser_path = _find_installed_browser()
    channel = _browser_channel_for(browser_path)
    headless = _env_flag('WHATSAPP_HEADLESS', 'false')

    launch_kwargs = {
        'user_data_dir': str(session_dir),
        'headless': headless,
        'viewport': {'width': 1280, 'height': 800},
        'args': [
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
            '--disable-dev-shm-usage',
        ],
        'ignore_default_args': ['--enable-automation'],
    }

    if browser_path:
        launch_kwargs['executable_path'] = browser_path
    else:
        launch_kwargs['channel'] = channel

    logger.info(
        'Launching WhatsApp browser for session %s (headless=%s, channel=%s)',
        session_dir.name, headless, launch_kwargs.get('executable_path') or channel,
    )
    return playwright.chromium.launch_persistent_context(**launch_kwargs)


def _screenshot(page, label, user_id):
    """
    Saves a debug screenshot, but only when explicitly asked for.

    These previously wrote unconditionally on every attempt, which filled
    MEDIA_ROOT with images of a logged-in WhatsApp session — in a directory
    that config/urls.py used to serve publicly.
    """
    if not _env_flag('WHATSAPP_DEBUG', 'false'):
        return None

    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        path = DEBUG_DIR / f'{label}_{user_id}_{int(time.time())}.png'
        page.screenshot(path=str(path))
        return path
    except Exception:
        logger.debug('Could not write debug screenshot', exc_info=True)
        return None


def _next_long_break_at(start_count=0):
    """How many messages from now the next long break lands (re-rolled each time)."""
    return start_count + random.randint(LONG_BREAK_MIN_INTERVAL, LONG_BREAK_MAX_INTERVAL)


def _is_logged_in(page):
    return page.locator(CHAT_LIST_SELECTOR).count() > 0


def _local_digits_for(number, calling_code):
    """Strips the calling code so WhatsApp infers the country from the dropdown."""
    digits = NON_DIGITS.sub('', number or '')
    if calling_code and digits.startswith(calling_code) and len(digits) > len(calling_code):
        return digits[len(calling_code):]
    return digits


def _fill_phone_login(page, whatsapp_number):
    """
    Enters the number into WhatsApp's login dialog.

    Two strategies, tried in order, because either one alone can fail:

      1. Select the country in the dropdown, then type only the national
         digits (WhatsApp infers the country code from the selection).
      2. Leave the dropdown alone and type the full international number
         including '+'.

    Strategy 1 is preferred but depends on resolving a display name that
    matches an option in WhatsApp's list. That resolution is range-based and
    can disagree with the number's region — +44 7911 numbers resolve to
    "Guernsey", a real answer that may or may not be in the list. Rather than
    trust it, we verify the field accepted the input and fall back to the
    international form, which sidesteps the dropdown entirely.

    Returns the string that ended up in the field.
    """
    digits = NON_DIGITS.sub('', whatsapp_number or '')
    if not digits:
        raise ValueError('WhatsApp number is empty.')

    country_name, calling_code = country_for(whatsapp_number)
    local_digits = _local_digits_for(whatsapp_number, calling_code)

    page.wait_for_selector(PHONE_INPUT_SELECTOR, timeout=30_000)
    phone_input = page.locator(PHONE_INPUT_SELECTOR).first

    if country_name:
        _select_country(page, country_name)

    entered = local_digits or digits
    phone_input.fill('')
    phone_input.fill(entered)
    page.wait_for_timeout(600)

    if not _phone_field_rejected(page):
        return entered

    # The number was rejected — almost always the country selection. Retry
    # with the full international form.
    logger.info(
        'Login number %s rejected after country selection (%s); retrying as international',
        entered, country_name,
    )
    international = '+' + digits
    phone_input.fill('')
    phone_input.fill(international)
    page.wait_for_timeout(600)

    if not _phone_field_rejected(page):
        return international

    # Neither form was accepted. Report it rather than clicking Next and
    # waiting 10 minutes for a code that will never arrive.
    raise ValueError(
        f'WhatsApp rejected "{international}" even with an explicit country code. '
        'Check that the number is a WhatsApp-capable mobile number.'
    )


def _select_country(page, country_name):
    """
    Clicks the country option, tolerating WhatsApp's markup changing.

    Returns True if an option was clicked. Force-click because the option
    list is frequently scrolled out of the viewport, which makes a
    coordinate-based click miss.
    """
    for candidate in (country_name, country_name.split(',')[0].strip()):
        if not candidate:
            continue
        option = page.locator(
            f'[role="option"]:has-text("{candidate}"), li:has-text("{candidate}"), '
            f'div:has-text("{candidate}")'
        ).first
        if option.count() > 0:
            try:
                option.click(force=True)
                page.wait_for_timeout(1200)
                logger.debug('Selected country %s', candidate)
                return True
            except Exception:
                logger.debug('Country option %s not clickable', candidate, exc_info=True)
    logger.info('Could not select country %s from the dropdown', country_name)
    return False


def _phone_field_rejected(page, field_selector=None):
    """True if WhatsApp is showing a validation error on the number field."""
    for selector in PHONE_REJECTION_SELECTORS:
        try:
            locator = page.locator(selector)
            if locator.count() > 0 and locator.first.is_visible():
                return True
        except Exception:
            continue
    return False


def _request_login_code(page, whatsapp_number, on_code, on_error):
    """
    Runs the linked-device handshake far enough to surface an 8-char code.

    Returns True if a code was produced. Any failure is reported through
    on_error rather than raised, because the caller still wants to wait for
    an already-linked session to finish initialising.
    """
    try:
        link_locator = page.locator(PHONE_LOGIN_LINK_SELECTOR).first
        # 45s, not 15s: the login box frequently hasn't painted when the load
        # event fires, and a cold Chromium launch on a slow connection eats
        # most of that budget.
        link_locator.wait_for(state='visible', timeout=45_000)

        _screenshot(page, 'before_click', whatsapp_number)

        # A DOM click rather than Playwright's: the link sits outside the
        # viewport or behind a transform often enough that a synthesized
        # click at its coordinates misses.
        link_locator.evaluate('node => node.click()')

        _screenshot(page, 'after_click', whatsapp_number)

        _fill_phone_login(page, whatsapp_number)

        next_button = page.locator(NEXT_BUTTON_SELECTOR).first
        next_button.wait_for(state='visible', timeout=15_000)
        next_button.click(force=True)

        # The code renders as separate characters, optionally split around a
        # dash, and its exact DOM structure changes between WhatsApp releases.
        # Reading visible text is far less brittle than targeting character
        # spans. The separator is optional between any two characters so all
        # the observed layouts match: ABCD-1234, ABCD 1234, ABCD1234.
        code = None
        deadline = time.time() + LOGIN_CODE_POLL_TIMEOUT_SECONDS
        while time.time() < deadline:
            text = page.locator('body').inner_text()
            match = LOGIN_CODE_PATTERN.search(text)
            if match:
                code = re.sub(r'[\s\-]', '', match.group(1)).strip()
                break
            page.wait_for_timeout(1000)

        if code:
            on_code(code)
            return True

        _screenshot(page, 'no_code', whatsapp_number)
        on_error(
            'WhatsApp accepted the number but never displayed a login code. '
            'This usually means WhatsApp changed their login page — try again in a few minutes.'
        )
        return False

    except Exception as exc:
        path = _screenshot(page, 'no_code', whatsapp_number)
        logger.exception('Could not obtain login code')
        on_error(f'Could not start WhatsApp login: {exc}' + (f' (screenshot: {path})' if path else ''))
        return False


def _wait_for_login(page, on_slow, on_failed):
    """
    Waits for the chat list to appear, i.e. login completed on the phone.

    Two stages so a slow-but-successful login gets a warning instead of an
    immediate failure, while a genuinely dead one still gives up.
    """
    try:
        page.wait_for_selector(CHAT_LIST_SELECTOR, timeout=LOGIN_SLOW_WARNING_MS)
        logger.info('WhatsApp logged in — chat list detected')
        return True
    except Exception:
        logger.warning('No login after %ds — flagging slow network', LOGIN_SLOW_WARNING_MS // 1000)
        on_slow()

    remaining = LOGIN_TOTAL_TIMEOUT_MS - LOGIN_SLOW_WARNING_MS
    try:
        page.wait_for_selector(CHAT_LIST_SELECTOR, timeout=remaining)
        logger.info('WhatsApp logged in after the slow-network warning')
        return True
    except Exception:
        logger.error('Gave up after %ds total — no login detected', LOGIN_TOTAL_TIMEOUT_MS // 1000)
        on_failed()
        return False


def _dismiss_welcome_popup(page):
    try:
        page.click('svg:has(title:text("ic-close"))', timeout=5000)
    except Exception:
        # No popup, or it was already dismissed. Not worth reporting.
        pass


def _attach_media(page, media_paths):
    """
    Attaches media files to the open chat.

    WhatsApp Web only accepts one file per attach action, so each file gets
    its own round trip through the file chooser.
    """
    for path in media_paths:
        try:
            page.click(ATTACH_BUTTON_SELECTOR, timeout=10_000)
            with page.expect_file_chooser(timeout=5000) as chooser_info:
                page.click(ATTACH_MENU_ITEM_SELECTOR, timeout=5000, force=True)
            chooser_info.value.set_files(path)
            page.wait_for_timeout(3000)
        except Exception:
            logger.warning('Could not attach media %s; sending text only', path, exc_info=True)
            return


def _send_to_contact(page, contact, message_text, media_paths, delay_mode):
    """Sends one message. Returns True on success."""
    phone = NON_DIGITS.sub('', contact['phone'])
    if not phone:
        logger.warning('Skipping contact %s — no digits in phone', contact.get('name'))
        return False

    personalized = message_text.replace('{name}', contact.get('name') or '')
    chat_url = (
        f'https://web.whatsapp.com/send?phone={phone}'
        f'&text={urllib.parse.quote(personalized)}'
    )

    try:
        page.goto(chat_url, timeout=120_000)
        page.wait_for_selector(SEND_ICON_SELECTOR, timeout=90_000)

        if media_paths:
            _attach_media(page, media_paths)

        page.click(f'{SEND_ICON_SELECTOR}:visible', timeout=10_000, force=True)
        return True
    except Exception:
        logger.warning('Send failed for %s (%s)', contact.get('name'), contact.get('phone'), exc_info=True)
        return False


def _mark_all(contacts, on_progress, status_value):
    """
    Settles every remaining contact's status.

    Without this, contacts that never got a turn stay 'pending' forever and
    the frontend's progress bar spins indefinitely instead of finishing.
    """
    if not on_progress:
        return
    for contact in contacts:
        on_progress(contact['id'], status_value)


def _sleep_between_messages(delay_seconds, sent_count, next_long_break_at):
    """
    Waits between sends, returning the (possibly re-rolled) next-break target.

    Instant mode deliberately gets none of the jitter — it is opt-in "send it
    all now", and mixing in long breaks would make it slower than delay mode
    while pretending to be fast.
    """
    if delay_seconds == INSTANT_DELAY_SECONDS:
        time.sleep(INSTANT_DELAY_SECONDS)
        return next_long_break_at

    if sent_count >= next_long_break_at:
        pause = random.uniform(LONG_BREAK_MIN_SECONDS, LONG_BREAK_MAX_SECONDS)
        logger.info('Pausing %.0fs after %d messages', pause, sent_count)
        time.sleep(pause)
        return _next_long_break_at(sent_count)

    time.sleep(random.uniform(DELAY_MIN_SECONDS, DELAY_MAX_SECONDS))
    return next_long_break_at


def send_whatsapp_messages(
    contacts,
    message_text,
    user_id,
    whatsapp_number,
    media_paths=None,
    delay_seconds=DELAY_MIN_SECONDS,
    on_progress=None,
    on_login_slow=None,
    on_login_failed=None,
    on_login_code=None,
    on_login_code_error=None,
    playwright_factory=None,
):
    """
    Links WhatsApp (if needed) and sends `message_text` to every contact.

    Returns a dict: {'sent': int, 'failed': int, 'logged_in': bool}.

    `on_login_code` / `on_login_code_error` / `on_login_slow` /
    `on_login_failed` let the caller persist handshake state to the database.

    `playwright_factory` exists so tests can substitute a fake browser; it
    defaults to Playwright's real sync API. Note that importing Playwright is
    deferred to here so that merely importing this module (which Django does
    at startup) doesn't pull in the whole browser automation stack.
    """
    contacts = list(contacts)

    # Escape hatch for exercising the API without touching WhatsApp at all.
    # Used by backend/smoke.py: hitting the real site from a test run means
    # launching a browser at web.whatsapp.com for every campaign, which is
    # slow, needs a display, and looks like abuse to WhatsApp.
    if _env_flag('WHATSAPP_DISABLE_AUTOMATION', 'false'):
        logger.warning(
            'WHATSAPP_DISABLE_AUTOMATION is set — marking %s contact(s) as sent '
            'without contacting WhatsApp. This is a test-only mode.',
            len(contacts),
        )
        for contact in contacts:
            if on_progress:
                on_progress(contact['id'], 'sent')
        return {'sent': len(contacts), 'failed': 0, 'logged_in': True}

    media_paths = media_paths or []

    def _noop(*_args, **_kwargs):
        return None

    on_progress = on_progress or _noop
    on_login_slow = on_login_slow or _noop
    on_login_failed = on_login_failed or _noop
    on_login_code = on_login_code or _noop
    on_login_code_error = on_login_code_error or _noop

    if not contacts:
        return {'sent': 0, 'failed': 0, 'logged_in': True}

    session_dir = get_session_dir(user_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    delay_mode = delay_seconds != INSTANT_DELAY_SECONDS

    if playwright_factory is None:
        from playwright.sync_api import sync_playwright

        playwright_factory = sync_playwright

    for attempt in range(SESSION_RETRIES + 1):
        # Wipe the profile on a retry: a half-linked session is exactly what
        # makes a second attempt fail the same way as the first.
        if attempt > 0:
            logger.info('Retrying send for user %s with a fresh session', user_id)
            reset_session(user_id)

        result = _one_pass(
            playwright_factory=playwright_factory,
            session_dir=session_dir,
            contacts=contacts,
            message_text=message_text,
            user_id=user_id,
            whatsapp_number=whatsapp_number,
            media_paths=media_paths,
            delay_seconds=delay_seconds,
            delay_mode=delay_mode,
            on_progress=on_progress,
            on_login_slow=on_login_slow,
            on_login_failed=on_login_failed,
            on_login_code=on_login_code,
            on_login_code_error=on_login_code_error,
        )

        if result['logged_in'] or attempt >= SESSION_RETRIES:
            return result

        logger.info('Session for user %s was not linked; retrying', user_id)

    return {'sent': 0, 'failed': len(contacts), 'logged_in': False}


def _one_pass(
    playwright_factory,
    session_dir,
    contacts,
    message_text,
    user_id,
    whatsapp_number,
    media_paths,
    delay_seconds,
    delay_mode,
    on_progress,
    on_login_slow,
    on_login_failed,
    on_login_code,
    on_login_code_error,
):
    """A single browser session: link if needed, then send. See send_whatsapp_messages."""
    from playwright.sync_api import Error as PlaywrightError

    sent = 0
    failed = 0

    with playwright_factory() as playwright:
        context = None
        try:
            context = _launch_browser_context(playwright, session_dir)
            page = context.pages[0] if context.pages else context.new_page()

            page.set_extra_http_headers({
                'sec-ch-ua': '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
            })
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined })"
            )

            try:
                page.goto('https://web.whatsapp.com', timeout=120_000)
                # goto() only waits for the load event; WhatsApp keeps
                # rendering the login box well past it. Waiting for network
                # idle gives the box time to paint before we look inside —
                # without this, the search for the phone-login link races the
                # page and loses.
                try:
                    page.wait_for_load_state('networkidle', timeout=20_000)
                except Exception:
                    # Non-fatal: WhatsApp polls in the background and never
                    # goes fully idle. The selector wait below does the real work.
                    pass
            except Exception as exc:
                logger.error('Could not load web.whatsapp.com for user %s: %s', user_id, exc)
                _mark_all(contacts, on_progress, 'failed')
                on_login_failed()
                return {'sent': 0, 'failed': len(contacts), 'logged_in': False}

            # Already-linked sessions skip the handshake entirely. This is
            # what makes a second campaign start instantly.
            if not _is_logged_in(page):
                _request_login_code(page, whatsapp_number, on_login_code, on_login_code_error)

            if not _wait_for_login(page, on_login_slow, on_login_failed):
                _mark_all(contacts, on_progress, 'failed')
                return {'sent': 0, 'failed': len(contacts), 'logged_in': False}

            _dismiss_welcome_popup(page)

            next_long_break_at = _next_long_break_at() if delay_mode else None

            for index, contact in enumerate(contacts):
                if _send_to_contact(page, contact, message_text, media_paths, delay_mode):
                    sent += 1
                    on_progress(contact['id'], 'sent')
                else:
                    failed += 1
                    on_progress(contact['id'], 'failed')

                next_long_break_at = _sleep_between_messages(
                    delay_seconds, index + 1, next_long_break_at
                )

            return {'sent': sent, 'failed': failed, 'logged_in': True}

        except PlaywrightError as exc:
            # A crashed browser (OOM, killed tab) shouldn't leave the whole
            # campaign looking like it never ran.
            logger.error('Browser error mid-send for user %s: %s', user_id, exc)
            _mark_all(
                [c for c in contacts if c.get('status') != 'sent'], on_progress, 'failed'
            )
            on_login_failed()
            return {'sent': sent, 'failed': failed + (len(contacts) - sent - failed), 'logged_in': False}
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    logger.debug('Error closing browser context', exc_info=True)