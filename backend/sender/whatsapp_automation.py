import time
import urllib.parse
from playwright.sync_api import sync_playwright


def send_whatsapp_messages(contacts, message_text, media_paths=None, delay_seconds=20, on_progress=None):
    media_paths = media_paths or []

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir="whatsapp_session",
            headless=False
        )
        page = browser.new_page()
        page.goto("https://web.whatsapp.com", timeout=120000)

        print("If a QR code shows up, scan it with your phone now. Waiting for WhatsApp to load (up to 2 minutes)...")
        try:
            page.wait_for_selector('[aria-label="Search or start a new chat"]', timeout=120000)
            print("WhatsApp loaded — chat list detected.")
        except Exception:
            print("Warning: chat list not detected within 2 minutes — continuing anyway.")

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
                if on_progress:
                    on_progress(contact['id'], 'failed')

            time.sleep(delay_seconds)

        browser.close()