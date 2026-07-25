import time
import os
import urllib.parse
from playwright.sync_api import sync_playwright

SESSIONS_DIR = os.path.join(os.path.dirname(__file__), '..', 'whatsapp_session')
MEDIA_DIR = os.path.join(os.path.dirname(__file__), '..', 'media')

# NOTE: these are ceilings, not fixed waits. wait_for_selector returns the
# instant the chat list appears — a fast scan still moves on in seconds.
# These only matter for people who get stuck.
LOGIN_SLOW_WARNING_MS = 300000   # 5 min — if still not logged in, flag "slow network"
LOGIN_TOTAL_TIMEOUT_MS = 600000  # 10 min — real ceiling before giving up entirely


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


def send_whatsapp_messages(contacts, message_text, user_id, whatsapp_number, media_paths=None,
                            delay_seconds=20, on_progress=None, on_login_slow=None):
    media_paths = media_paths or []
    login_code_path = get_login_code_path(user_id)
    session_dir = get_session_dir(user_id)

    with sync_playwright() as p:
        # Stays headless — the code is meant to be read from the web app,
        # not from a popped-up browser window on the machine running this
        # server.
        browser = p.chromium.launch_persistent_context(
            user_data_dir=session_dir,
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            extra_http_headers={
                "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            },
        )
        page = browser.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined })"
        )

        try:
            page.goto("https://web.whatsapp.com", timeout=120000)
        except Exception as e:
            print(f"Failed to load web.whatsapp.com for user {user_id}: {e}")
            browser.close()
            return

        # --- Phone-number login instead of QR scan ---
        # WhatsApp Web's default screen offers a "Log in with phone number"
        # link next to the QR code. Clicking it and entering the number
        # generates an 8-character code HERE, in this browser session —
        # that's the code the person then types into their phone under
        # Linked Devices to confirm the link. Much easier to serve
        # reliably through the app UI than a QR screenshot that depends
        # on the canvas actually rendering.
        #
        # NOTE: the selectors below are WhatsApp Web's DOM structure as of
        # this writing. WhatsApp changes this UI periodically without
        # notice, so if this block throws, check the debug screenshot it
        # saves and adjust the selectors to match what's actually on the
        # page — this is the one part of this flow that needs live
        # verification, not something fixable from code alone.
        try:
            page.click('text=Log in with phone number', timeout=30000)

            # whatsapp_number is stored as "+<countrycode><number>" (see
            # UserProfile / the phone regex in views.py). WhatsApp's input
            # here expects just the digits after the '+' — it has its own
            # country selector, but pasting the full digit string usually
            # gets auto-parsed correctly. Worth confirming visually the
            # first time this runs.
            digits = whatsapp_number.lstrip('+')
            phone_input = page.wait_for_selector(
                'input[aria-label="Type your phone number."]', timeout=15000
            )
            phone_input.fill(digits)
            page.click('div[role="button"]:has-text("Next")', timeout=10000)

            # The code WhatsApp displays here is what gets typed into the
            # phone, not something coming from it. Grabbing the container's
            # text rather than individual character elements, since
            # WhatsApp sometimes renders each character in its own span
            # and the exact structure is the part most likely to have
            # shifted by the time this runs.
            code_container = page.wait_for_selector(
                '[data-testid="link-with-phone-number-code"]', timeout=30000
            )
            code_text = code_container.inner_text().strip()

            os.makedirs(os.path.dirname(login_code_path), exist_ok=True)
            with open(login_code_path, 'w') as f:
                f.write(code_text)
            print(f"Login code generated for user {user_id}: {code_text}")

        except Exception as e:
            debug_path = os.path.join(os.path.dirname(login_code_path), f'debug_no_code_{user_id}.png')
            os.makedirs(os.path.dirname(debug_path), exist_ok=True)
            try:
                page.screenshot(path=debug_path)
                print(f"Could not get login code for user {user_id}: {e}. Saved page state to {debug_path} for debugging.")
            except Exception:
                print(f"Could not get login code for user {user_id}: {e}. Could not capture debug screenshot either.")

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

        for contact in contacts:
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

            time.sleep(delay_seconds)

        browser.close()