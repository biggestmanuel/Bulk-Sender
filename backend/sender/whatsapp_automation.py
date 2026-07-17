import time
import os
import urllib.parse
from playwright.sync_api import sync_playwright

SESSIONS_DIR = os.path.join(os.path.dirname(__file__), '..', 'whatsapp_session')
MEDIA_DIR = os.path.join(os.path.dirname(__file__), '..', 'media')

# NOTE: these are ceilings, not fixed waits. wait_for_selector returns the
# instant the chat list appears — a fast scan still moves on in seconds.
# These only matter for people who get stuck.
QR_SLOW_WARNING_MS = 300000   # 5 min — if still not logged in, flag "slow network"
QR_TOTAL_TIMEOUT_MS = 600000  # 10 min — real ceiling before giving up entirely


def get_session_dir(user_id):
    """
    Each user gets their own persistent browser profile directory, so
    logins and in-progress QR scans never collide between two people
    sending campaigns at the same time.
    """
    return os.path.join(SESSIONS_DIR, f'user_{user_id}')


def get_qr_path(user_id):
    """Per-user QR screenshot path, so the frontend never shows one user's
    QR code to another user polling at the same time."""
    return os.path.join(MEDIA_DIR, f'qr_code_{user_id}.png')


def send_whatsapp_messages(contacts, message_text, user_id, media_paths=None,
                            delay_seconds=20, on_progress=None, on_qr_slow=None):
    media_paths = media_paths or []
    qr_path = get_qr_path(user_id)
    session_dir = get_session_dir(user_id)

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=session_dir,
            headless=True
        )
        page = browser.new_page()
        page.goto("https://web.whatsapp.com", timeout=120000)

        # If a QR code shows up (first login / expired session), screenshot it
        try:
            qr_canvas = page.wait_for_selector('canvas', timeout=15000)
            os.makedirs(os.path.dirname(qr_path), exist_ok=True)
            qr_canvas.screenshot(path=qr_path)
            print(f"QR code screenshot saved for user {user_id} — waiting for it to be scanned.")
        except Exception:
            print(f"No QR canvas found for user {user_id} — likely already logged in from a saved session.")

        # STAGE 1: wait up to 5 minutes for login. Returns immediately on success —
        # this is NOT a fixed 5-minute delay, just the max wait before we warn.
        logged_in = False
        try:
            page.wait_for_selector('[aria-label="Search or start a new chat"]', timeout=QR_SLOW_WARNING_MS)
            print("WhatsApp loaded — chat list detected.")
            logged_in = True
        except Exception:
            print("Still waiting on login after 5 minutes — flagging slow network.")
            if on_qr_slow:
                on_qr_slow()

        # STAGE 2: only runs if stage 1 timed out. Waits the remaining time
        # before actually giving up. Still exits immediately on success.
        if not logged_in:
            remaining_ms = QR_TOTAL_TIMEOUT_MS - QR_SLOW_WARNING_MS
            try:
                page.wait_for_selector('[aria-label="Search or start a new chat"]', timeout=remaining_ms)
                print("WhatsApp loaded after the slow-network warning.")
                logged_in = True
            except Exception:
                print("Gave up after 10 minutes total — no login detected. Aborting send.")
                browser.close()
                return

        # Logged in — remove the QR screenshot so the frontend knows to stop showing it
        if os.path.exists(qr_path):
            os.remove(qr_path)

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