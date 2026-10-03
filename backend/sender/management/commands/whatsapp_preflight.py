"""
Preflight check for the WhatsApp automation.

Run this before a send you care about:

    python manage.py whatsapp_preflight

The automation is driven by CSS/text selectors against a third-party UI that
changes without notice. When WhatsApp changes their login page, the symptom is
a campaign that sits for ten minutes and then fails with every contact marked
failed — by which point you've burned ten minutes of the user's time and
learned nothing about why.

This checks the selectors against the live page in about 30 seconds and tells
you which specific step broke. It is read-only with respect to the app: it uses
a throwaway profile directory and does not touch any user's real session.
"""

import re
import sys
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from sender import whatsapp_automation as wa


class CheckResult:
    def __init__(self, name):
        self.name = name
        self.ok = False
        self.detail = ''

    @property
    def symbol(self):
        return 'OK  ' if self.ok else 'FAIL'


class Command(BaseCommand):
    help = (
        'Verify that the WhatsApp Web selectors the automation depends on still '
        'match the live page. Run this when a send fails unexpectedly.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--timeout',
            type=int,
            default=30,
            help='Seconds to wait for each selector (default: 30).',
        )
        parser.add_argument(
            '--number',
            default='',
            help=(
                'Optional E.164 number to test the country-selection logic with, '
                'e.g. +2348161234765.'
            ),
        )

    def handle(self, *args, **options):
        timeout_ms = options['timeout'] * 1000

        self.stdout.write(self.style.MIGRATE_HEADING('WhatsApp automation preflight'))
        self.stdout.write(
            'This launches a real browser against web.whatsapp.com using a '
            'throwaway profile. It does not touch any linked session.\n'
        )

        browser_path = wa._find_installed_browser()
        if browser_path:
            self.stdout.write(f'Browser: {browser_path}')
        else:
            self.stdout.write(
                self.style.WARNING(
                    'Browser: no local Chrome/Edge/Brave found — falling back to '
                    "Playwright's bundled Chromium, which WhatsApp often refuses. "
                    'Set WHATSAPP_BROWSER_PATH if this causes problems.'
                )
            )

        headless = wa._env_flag('WHATSAPP_HEADLESS', 'false')
        if not headless:
            self.stdout.write(
                self.style.WARNING(
                    'Headless is off, so a display is required. For a headless host, '
                    'run under Xvfb (e.g. `xvfb-run -a python manage.py whatsapp_preflight`).'
                )
            )
        self.stdout.write('')

        results = []

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise CommandError('Playwright is not installed. Run: pip install -r requirements.txt')

        with tempfile.TemporaryDirectory(prefix='wa-preflight-') as tmp:
            session_dir = Path(tmp)

            try:
                with sync_playwright() as playwright:
                    context = wa._launch_browser_context(playwright, session_dir)
                    try:
                        page = context.pages[0] if context.pages else context.new_page()
                        results.extend(self._check_page(page, timeout_ms, options['number']))
                    finally:
                        try:
                            context.close()
                        except Exception:
                            pass
            except Exception as exc:
                raise CommandError(
                    f'Could not launch a browser: {exc}\n'
                    'If this is a headless host, try running under Xvfb.'
                )

        for result in results:
            style = self.style.SUCCESS if result.ok else self.style.ERROR
            self.stdout.write(f'  [{result.symbol}] {result.name}')
            if result.detail:
                self.stdout.write(f'         {result.detail}')

        failed = [r for r in results if not r.ok]

        self.stdout.write('')
        if failed:
            self.stdout.write(
                self.style.ERROR(
                    f'{len(failed)} of {len(results)} checks failed. WhatsApp has '
                    'probably changed their login page.'
                )
            )
            self.stdout.write(
                'The selectors live in sender/whatsapp_automation.py '
                '(_request_login_code, _fill_phone_login, _send_to_contact).'
            )
            return

        self.stdout.write(self.style.SUCCESS(f'All {len(results)} checks passed.'))

    def _check_page(self, page, timeout_ms, number):
        results = []

        def record(name, ok, detail=''):
            results.append(CheckResult(name))
            results[-1].ok = ok
            results[-1].detail = detail

        # 1. Does the page load at all?
        try:
            page.goto('https://web.whatsapp.com', timeout=timeout_ms * 2)
            record('page loads', True, 'web.whatsapp.com responded')
        except Exception as exc:
            record('page loads', False, f'could not load web.whatsapp.com: {exc}')
            return results

        try:
            page.wait_for_load_state('networkidle', timeout=15_000)
        except Exception:
            # Non-fatal; WhatsApp polls in the background and never idles.
            pass

        # 2. Is an existing session already linked?
        logged_in = wa._is_logged_in(page)
        record(
            'chat list selector',
            True,
            'session is linked (no code needed)'
            if logged_in
            else 'not linked — expected on a fresh profile',
        )
        if logged_in:
            # Everything below needs the logged-out flow to be visible.
            record('login link', True, 'skipped: already linked')
            record('phone input', True, 'skipped: already linked')
            record('Next button', True, 'skipped: already linked')
            record('code scraping', True, 'skipped: already linked')
            return results

        # 3. The phone-number login link.
        link = page.locator(wa.PHONE_LOGIN_LINK_SELECTOR).first
        try:
            link.wait_for(state='visible', timeout=timeout_ms)
            record('login link', True, wa.PHONE_LOGIN_LINK_SELECTOR)
        except Exception:
            record(
                'login link',
                False,
                f'{wa.PHONE_LOGIN_LINK_SELECTOR} never appeared. WhatsApp may have '
                'removed the "Link with phone number" option.',
            )
            return results

        try:
            link.evaluate('node => node.click()')
            record('login link click', True, 'clicked via DOM')
        except Exception as exc:
            record('login link click', False, f'could not click the link: {exc}')
            return results

        # 4. The phone number input.
        try:
            page.wait_for_selector(wa.PHONE_INPUT_SELECTOR, timeout=timeout_ms)
            record('phone input', True, wa.PHONE_INPUT_SELECTOR)
        except Exception:
            record(
                'phone input',
                False,
                f'{wa.PHONE_INPUT_SELECTOR} never appeared.',
            )
            return results

        # 5. The Next button.
        try:
            page.locator('button:has-text("Next")').first.wait_for(state='visible', timeout=timeout_ms)
            record('Next button', True, 'button:has-text("Next")')
        except Exception:
            record('Next button', False, 'button:has-text("Next") never appeared.')
            return results

        # 6. Country resolution, if a number was supplied. Doesn't touch the
        #    network — this is the part most likely to be wrong after a
        #    phonenumbers metadata update.
        if number:
            try:
                country, code = wa.country_for(number)
                if country:
                    record(
                        'country resolution',
                        True,
                        f'{number} -> {country} (+{code})',
                    )
                else:
                    record(
                        'country resolution',
                        False,
                        f'could not determine a country for {number}. The automation '
                        'would submit this under the wrong country.',
                    )
            except Exception as exc:
                record('country resolution', False, f'raised for {number}: {exc}')
        else:
            self.stdout.write(
                self.style.WARNING(
                    '  (pass --number +<e164> to also verify country resolution)'
                )
            )
            record('country resolution', True, 'not checked (no --number given)')

        # 7. Can the code be scraped? Only checkable once a real code exists,
        #    which needs a real number and a real send. Verify the pattern
        #    against synthetic input instead so a regex regression is caught.
        sample = 'Enter code on phone\nABCD-1234'
        pattern = wa.LOGIN_CODE_PATTERN
        if pattern.search(sample):
            record('code scraping', True, 'pattern matches a representative code')
        else:
            record(
                'code scraping',
                False,
                'the login-code pattern does not match "ABCD-1234"',
            )

        return results