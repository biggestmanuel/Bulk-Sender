import re
import time
import os
import random
import shutil
import urllib.parse
from playwright.sync_api import sync_playwright

SESSIONS_DIR = os.path.join(os.path.dirname(__file__), '..', 'whatsapp_session')
MEDIA_DIR = os.path.join(os.path.dirname(__file__), '..', 'media')


def _find_installed_browser():
    """Prefer a real local Chrome/Edge installation over Playwright's bundled Chromium.

    WhatsApp Web is strict about the browser it will accept for the linked-device flow.
    A manually-installed Chrome or Edge is much more reliable than the headless bundled
    Chromium used by Playwright, especially in environments that will later be used to
    complete the phone-link handshake.
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


def _launch_browser_context(p, session_dir):
    browser_path = _find_installed_browser()
    channel = _browser_channel_for(browser_path)
    headless = os.environ.get('WHATSAPP_HEADLESS', 'false').strip().lower() == 'true'

    launch_kwargs = {
        'user_data_dir': session_dir,
        'headless': headless,
        'viewport': {'width': 1280, 'height': 800},
        'args': ['--disable-blink-features=AutomationControlled'],
        'ignore_default_args': ['--enable-automation'],
    }

    if browser_path:
        launch_kwargs['executable_path'] = browser_path
    else:
        launch_kwargs['channel'] = channel

    return p.chromium.launch_persistent_context(**launch_kwargs)

# NOTE: these are ceilings, not fixed waits. wait_for_selector returns the
# instant the chat list appears — a fast scan still moves on in seconds.
# These only matter for people who get stuck.
LOGIN_SLOW_WARNING_MS = 300000   # 5 min — if still not logged in, flag "slow network"
LOGIN_TOTAL_TIMEOUT_MS = 600000  # 10 min — real ceiling before giving up entirely

COUNTRY_CODES = {
    '234': 'Nigeria',
    '233': 'Ghana',
    '254': 'Kenya',
    '250': 'Rwanda',
    '255': 'Tanzania',
    '256': 'Uganda',
    '1': 'United States',
    '44': 'United Kingdom',
    '27': 'South Africa',
    '61': 'Australia',
    '49': 'Germany',
    '31': 'Netherlands',
    '32': 'Belgium',
    '33': 'France',
    '41': 'Switzerland',
    '7': 'Russia',
    '91': 'India',
    '92': 'Pakistan',
    '971': 'United Arab Emirates',
}


def _country_name_for_phone(phone_number):
    digits = re.sub(r'\D', '', phone_number or '')
    if not digits:
        return None, ''

    for code in sorted(COUNTRY_CODES, key=len, reverse=True):
        if digits.startswith(code):
            return COUNTRY_CODES[code], code
    return None, ''


def _fill_phone_login(page, whatsapp_number):
    digits = re.sub(r'\D', '', whatsapp_number or '')
    if not digits:
        raise ValueError('WhatsApp number is empty.')

    country_name, code = _country_name_for_phone(whatsapp_number)
    local_digits = digits[len(code):] if code else digits

    selector = '[data-testid="phone-number-input"], input[autocomplete="tel"], input[type="tel"]'
    phone_input = page.wait_for_selector(selector, timeout=30000)

    if country_name:
        country_button = page.locator(f'button:has-text("{country_name}")').first
        if country_button.count() > 0:
            try:
                country_button.click(force=True)
                page.wait_for_timeout(1500)
            except Exception:
                pass

    phone_input.fill('')
    phone_input.fill(local_digits or digits)
    page.wait_for_timeout(500)
    return local_digits or digits

# --- Delay-mode pacing ---
# A fixed gap between every message (even a "random" one drawn from a
# narrow band) is itself a bot signal — no person sends thousands of
# messages back-to-back with zero breaks, varied or not. This aims for a
# looser, more human rhythm: a wider randomized base gap, plus an
# occasional longer pause every 20-30 messages, as if someone stepped away
# for a bit. None of this guarantees avoiding detection — it just moves
# further from "obviously scripted" than a flat delay does.
DELAY_MIN_SECONDS = 5
DELAY_MAX_SECONDS = 15
LONG_BREAK_MIN_INTERVAL = 20   # messages
LONG_BREAK_MAX_INTERVAL = 30   # messages
LONG_BREAK_MIN_SECONDS = 45
LONG_BREAK_MAX_SECONDS = 120


def _next_long_break_at(start_count=0):
    """Picks how many messages from now the next long break should land,
    re-rolled each time a break happens so the interval itself isn't fixed
    either."""
    return start_count + random.randint(LONG_BREAK_MIN_INTERVAL, LONG_BREAK_MAX_INTERVAL)


def get_session_dir(user_id):
    """
    Each user gets their own persistent browser profile directory, so
    logins and in-progress code entry never collide between two people
    sending campaigns at the same time.
    """
    return os.path.join(SESSIONS_DIR, f'user_{user_id}')


def get_login_code_path(user_id):
    """Per-user login-code text path, so the frontend never shows one
    user's code to another user polling at the same time."""
    return os.path.join(MEDIA_DIR, f'login_code_{user_id}.txt')


def _mark_all_failed(contacts, on_progress):
    """
    Used on both give-up paths below. Without this, contacts that never
    got a chance to send just sit at 'pending' forever, which makes the
    frontend's progress polling spin indefinitely instead of reporting
    the send as failed.
    """
    if not on_progress:
        return
    for contact in contacts:
        on_progress(contact['id'], 'failed')


def send_whatsapp_messages(contacts, message_text, user_id, whatsapp_number, media_paths=None,
                            delay_seconds=20, on_progress=None, on_login_slow=None, on_login_failed=None,
                            on_login_code_error=None):
    media_paths = media_paths or []
    login_code_path = get_login_code_path(user_id)
    session_dir = get_session_dir(user_id)
    if os.path.exists(session_dir):
        shutil.rmtree(session_dir)
    os.makedirs(session_dir, exist_ok=True)

    with sync_playwright() as p:
        browser = _launch_browser_context(p, session_dir)
        page = browser.new_page()
        page.set_extra_http_headers({
            "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        })
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined })"
        )

        try:
            page.goto("https://web.whatsapp.com", timeout=120000)
            # goto() only waits for the 'load' event, but WhatsApp Web keeps
            # rendering (QR/login box) well after that — a debug screenshot
            # caught the page still showing its loading spinner over the QR
            # box at the point the phone-login link search began, which is
            # the actual root cause of the "code never shows up" symptom.
            # Waiting for network activity to settle first gives the login
            # box time to actually paint before we go looking inside it.
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                # Not fatal — some connections never go fully idle (WhatsApp
                # polls in the background). Fall through and let the
                # longer selector wait below do the real work.
                pass
        except Exception as e:
            print(f"Failed to load web.whatsapp.com for user {user_id}: {e}")
            _mark_all_failed(contacts, on_progress)
            if on_login_failed:
                on_login_failed()
            browser.close()
            return

        # --- Phone-number login instead of QR scan ---
        # WhatsApp Web's default screen offers a "Log in with phone number"
        # link next to the QR code. Clicking it and entering the number
        # generates an 8-character code HERE, in this browser session —
        # that's the code the person then types into their phone under
        # Linked Devices to confirm the link. Selectors below were pulled
        # from a live inspection of the actual page (not guessed), except
        # where noted.
        #
        # Timeout raised from 15s -> 45s: a debug screenshot showed the page
        # still mid-load (QR spinner active, login box not fully painted)
        # at the point this selector search was timing out — 15s wasn't
        # enough margin on a slower connection or cold Chromium launch.
        try:
            login_link = page.locator('text=/Link with phone number instead|Log in with phone number/').first
            login_link.wait_for(state='visible', timeout=45000)

            # Debug: confirms the link is actually visible/positioned
            # before we click it.
            before_click_path = os.path.join(os.path.dirname(login_code_path), f'debug_before_click_{user_id}.png')
            os.makedirs(os.path.dirname(before_click_path), exist_ok=True)
            page.screenshot(path=before_click_path)

            # Playwright's normal click can fail when the link sits outside the
            # viewport or is positioned by a transform. Force the click through
            # the DOM itself, which is how the QA probe succeeded against the
            # real WhatsApp page.
            login_link.evaluate("node => node.click()")

            # Debug: confirms whether the click above actually navigated
            # to the phone-number screen.
            after_click_path = os.path.join(os.path.dirname(login_code_path), f'debug_after_click_{user_id}.png')
            page.screenshot(path=after_click_path)

            # WhatsApp expects the country to be selected before it accepts the
            # national number. We select the matching country by the saved phone's
            # calling code, then supply only the local digits so WhatsApp infers the
            # same country code automatically instead of needing a manual re-entry.
            _fill_phone_login(page, whatsapp_number)

            next_button = page.locator('button:has-text("Next")').first
            next_button.wait_for(state='visible', timeout=15000)
            next_button.click(force=True)

            # The code is displayed as individual characters split around a dash,
            # not as a single clean string. Read the visible page text and extract
            # the code-like segment after "Enter code on phone". This is enough
            # to show the user what to type on their phone, without assuming a
            # brittle DOM structure that changes across WhatsApp versions.
            page_text = page.locator('body').inner_text()
            code_match = re.search(r'Enter code on phone.*?([A-Z0-9]{2}(?:\s*[-\s]\s*[A-Z0-9]){5,7})', page_text, flags=re.S | re.I)
            code_text = code_match.group(1).replace('-', '').replace(' ', '') if code_match else None
            if code_text:
                os.makedirs(os.path.dirname(login_code_path), exist_ok=True)
                with open(login_code_path, 'w') as f:
                    f.write(code_text)
                print(f"Login code generated for user {user_id}: {code_text}")
            else:
                print(f"Could not parse login code from page for user {user_id}. Page text: {page_text[:1000]}")

        except Exception as e:
            debug_path = os.path.join(os.path.dirname(login_code_path), f'debug_no_code_{user_id}.png')
            os.makedirs(os.path.dirname(debug_path), exist_ok=True)
            try:
                page.screenshot(path=debug_path)
                print(f"Could not get login code for user {user_id}: {e}. Saved page state to {debug_path} for debugging.")
            except Exception:
                print(f"Could not get login code for user {user_id}: {e}. Could not capture debug screenshot either.")

            # Previously this failure was console/screenshot-only — the
            # frontend had no signal, so LoginCode.jsx just showed a blank
            # overlay forever until the unrelated 10-minute login timeout
            # eventually fired. Surface it immediately instead.
            if on_login_code_error:
                on_login_code_error(str(e))

        # STAGE 1: wait up to 5 minutes for login. Returns immediately on success —
        # this is NOT a fixed 5-minute delay, just the max wait before we warn.
        logged_in = False
        try:
            page.wait_for_selector('[aria-label="Search or start a new chat"]', timeout=LOGIN_SLOW_WARNING_MS)
            print("WhatsApp loaded — chat list detected.")
            logged_in = True
        except Exception:
            print("Still waiting on login after 5 minutes — flagging slow network.")
            if on_login_slow:
                on_login_slow()

        # STAGE 2: only runs if stage 1 timed out. Waits the remaining time
        # before actually giving up. Still exits immediately on success.
        if not logged_in:
            remaining_ms = LOGIN_TOTAL_TIMEOUT_MS - LOGIN_SLOW_WARNING_MS
            try:
                page.wait_for_selector('[aria-label="Search or start a new chat"]', timeout=remaining_ms)
                print("WhatsApp loaded after the slow-network warning.")
                logged_in = True
            except Exception:
                print("Gave up after 10 minutes total — no login detected. Aborting send.")
                _mark_all_failed(contacts, on_progress)
                if on_login_failed:
                    on_login_failed()
                browser.close()
                return

        # Logged in — remove the login code so the frontend knows to stop showing it
        if os.path.exists(login_code_path):
            os.remove(login_code_path)

        # Dismiss the "Welcome to WhatsApp Web" popup if it shows up
        try:
            page.click('svg:has(title:text("ic-close"))', timeout=5000)
            print("Dismissed welcome popup.")
        except Exception:
            print("No welcome popup found (or already dismissed) — continuing.")

        # Only apply jitter/long-breaks in delay mode. Instant mode is
        # meant to be fast and stays exactly as before — delay_seconds
        # (1s) is used directly for it, untouched.
        is_delay_mode = delay_seconds != 1
        next_long_break_at = _next_long_break_at() if is_delay_mode else None

        for i, contact in enumerate(contacts):
            phone = contact['phone'].replace('+', '').replace(' ', '')
            personalized_message = message_text.replace('{name}', contact['name'])
            encoded_message = urllib.parse.quote(personalized_message)
            chat_url = f"https://web.whatsapp.com/send?phone={phone}&text={encoded_message}"

            try:
                page.goto(chat_url, timeout=120000)
                page.wait_for_selector('[data-testid="wds-ic-send-filled"]', timeout=90000)

                if media_paths:
                    page.click('[data-testid="plus-rounded"]', timeout=10000)

                    with page.expect_file_chooser() as fc_info:
                        page.click('text=Photos & videos', timeout=3000, force=True)
                    file_chooser = fc_info.value
                    file_chooser.set_files(media_paths)

                    page.wait_for_timeout(5000)

                page.click('[data-testid="wds-ic-send-filled"]:visible', timeout=10000, force=True)
                if on_progress:
                    on_progress(contact['id'], 'sent')

            except Exception as e:
                print(f"Failed to send to {contact['name']} ({contact['phone']}): {e}")
                if on_progress:
                    on_progress(contact['id'], 'failed')

            if not is_delay_mode:
                time.sleep(delay_seconds)
                continue

            messages_sent = i + 1
            if messages_sent >= next_long_break_at:
                pause = random.uniform(LONG_BREAK_MIN_SECONDS, LONG_BREAK_MAX_SECONDS)
                print(f"Taking a longer pause ({pause:.0f}s) after {messages_sent} messages.")
                time.sleep(pause)
                next_long_break_at = _next_long_break_at(messages_sent)
            else:
                time.sleep(random.uniform(DELAY_MIN_SECONDS, DELAY_MAX_SECONDS))

        browser.close()