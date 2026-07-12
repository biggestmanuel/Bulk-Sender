import time
import os
import re
import urllib.parse
from datetime import datetime
from playwright.sync_api import sync_playwright

# Saves the QR screenshot into backend/media/qr_code.png
QR_PATH = os.path.join(os.path.dirname(__file__), '..', 'media', 'qr_code.png')

# Failure screenshots go into backend/media/failures/
FAILURES_DIR = os.path.join(os.path.dirname(__file__), '..', 'media', 'failures')

# NOTE: these are ceilings, not fixed waits. wait_for_selector returns the
# instant the chat list appears — a fast scan still moves on in seconds.
# These only matter for people who get stuck.
QR_SLOW_WARNING_MS = 300000   # 5 min — if still not logged in, flag "slow network"
QR_TOTAL_TIMEOUT_MS = 600000  # 10 min — real ceiling before giving up entirely


def _safe_filename_part(text):
    """Strips anything that isn't safe for a filename (keeps letters, numbers, - and _)."""
    return re.sub(r'[^a-zA-Z0-9_-]', '', text.replace(' ', '_'))


def _save_failure_screenshot(page, contact):
    """
    Takes a screenshot of the current page state when a send fails,
    so you can see what WhatsApp Web actually looked like at that moment
    instead of debugging blind from print() logs alone.
    """
    try:
        os.makedirs(FAILURES_DIR, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        name_part = _safe_filename_part(contact.get('name', 'unknown'))
        phone_part = _safe_filename_part(contact.get('phone', 'unknown'))
        filename = f"failure_{name_part}_{phone_part}_{timestamp}.png"
        filepath = os.path.join(FAILURES_DIR, filename)
        page.screenshot(path=filepath)
        print(f"Saved failure screenshot: {filename}")
        return filename
    except Exception as screenshot_err:
        print(f"Could not save failure screenshot: {screenshot_err}")
        return None


def send_whatsapp_messages(contacts, message_text, media_paths=None, delay_seconds=20, on_progress=None, on_qr_slow=None):
    media_paths = media_paths or []

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir="whatsapp_session",
            headless=True
        )
        page = browser.new_page()
        page.goto("https://web.whatsapp.com", timeout=120000)

        # If a QR code shows up (first login / expired session), screenshot it
        try:
            qr_canvas = page.wait_for_selector('canvas', timeout=15000)
            os.makedirs(os.path.dirname(QR_PATH), exist_ok=True)
            qr_canvas.screenshot(path=QR_PATH)
            print("QR code screenshot saved — waiting for it to be scanned.")
        except Exception:
            print("No QR canvas found — likely already logged in from a saved session.")

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
        if os.path.exists(QR_PATH):
            os.remove(QR_PATH)

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
                    print(f"Attempting to attach {len(media_paths)} media file(s): {media_paths}")
                    page.click('[data-testid="plus-rounded"]', timeout=10000)
                    print("Clicked + button")

                    with page.expect_file_chooser() as fc_info:
                        page.click('text=Photos & videos', timeout=3000, force=True)
                    print("Clicked Photos & videos, file chooser intercepted")
                    file_chooser = fc_info.value
                    file_chooser.set_files(media_paths)
                    print("Files set into chooser")

                    page.wait_for_timeout(5000)
                    print("Waited after setting files")

                page.click('[data-testid="wds-ic-send-filled"]:visible', timeout=10000, force=True)
                if on_progress:
                    on_progress(contact['id'], 'sent')

            except Exception as e:
                print(f"Failed to send to {contact['name']} ({contact['phone']}): {e}")
                _save_failure_screenshot(page, contact)
                if on_progress:
                    on_progress(contact['id'], 'failed')

            time.sleep(delay_seconds)

        browser.close()