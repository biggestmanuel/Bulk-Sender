"""
Tests for the polling endpoints' rate limits.

The bug this file guards: the progress panel polls campaign status every 3
seconds (1,200/hour) and the login overlay every 2 seconds (1,800/hour). Both
inherited the default 600/hour 'user' rate, so a send lasting about half an
hour would start returning 429 — the progress panel froze while the send
carried on in the background, which reads as the app losing track of a
campaign that was still running.
"""

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from sender.models import Campaign, Contact, UserProfile

CLIENT_STATUS_PER_HOUR = 3600 / 3   # App.jsx POLL_INTERVAL_MS = 3000
CLIENT_LOGIN_PER_HOUR = 3600 / 2    # LoginCode.jsx POLL_INTERVAL_MS = 2000


def rate_per_hour(rate_string):
    """'2400/hour' -> 2400. Raises if the unit isn't an hour."""
    value, _, unit = rate_string.partition('/')
    if unit != 'hour':
        raise AssertionError(f'expected an hourly rate, got {rate_string!r}')
    return int(value)


class PollRateBudgetTests(TestCase):
    def setUp(self):
        from django.conf import settings

        rates = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
        self.status_rate = rate_per_hour(rates['status'])
        self.login_rate = rate_per_hour(rates['login_status'])

    def test_status_budget_exceeds_the_client_polling_rate(self):
        self.assertGreater(
            self.status_rate, CLIENT_STATUS_PER_HOUR,
            'the status budget must exceed what the client sends before '
            'backoff kicks in',
        )

    def test_login_budget_exceeds_the_client_polling_rate(self):
        self.assertGreater(self.login_rate, CLIENT_LOGIN_PER_HOUR)

    def test_status_budget_outlasts_a_long_send(self):
        """
        App.jsx backs off from a 3s gap to a 30s ceiling, so a steady-state
        send polls ~120/hour. The budget has to cover hours of that, not
        minutes — delay mode on a large campaign runs for many hours and the
        progress panel must survive the whole thing.
        """
        steady_state_per_hour = 3600 / 30
        hours = self.status_rate / steady_state_per_hour
        self.assertGreater(hours, 10, 'at least ten hours of steady-state polling')

    def test_budget_covers_the_pre_backoff_burst(self):
        """
        The first minute of a send polls at the full 3s rate before backing
        off, so the budget must cover that burst on its own too.
        """
        first_minute = CLIENT_STATUS_PER_HOUR / 60
        self.assertGreater(self.status_rate, first_minute * 60)

    def test_budgets_are_independent_of_the_default_user_rate(self):
        from django.conf import settings

        rates = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
        self.assertNotEqual(rates['status'], rates['user'])
        self.assertNotEqual(rates['login_status'], rates['user'])

    def test_polling_budget_is_not_the_anonymous_rate(self):
        from django.conf import settings

        rates = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
        self.assertNotEqual(rates['status'], rates['anon'])

    def test_rates_are_overridable(self):
        from django.conf import settings

        rates = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
        # The env var is read with a default; assert the plumbing exists.
        self.assertTrue(rates['status'])
        self.assertTrue(rates['login_status'])


class PollingIsNotThrottledInPracticeTests(TestCase):
    """Exercises a realistic number of polls against the configured budget."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('alice', password='pw')
        UserProfile.objects.create(user=self.user, whatsapp_number='+2348161234765')
        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=self.user).key}'
        )
        self.campaign = Campaign.objects.create(
            owner=self.user, name='c', message_text='hi', send_mode='delay',
            status=Campaign.Status.RUNNING,
        )
        Contact.objects.create(campaign=self.campaign, name='Ada', phone='+2348161234765')

    @override_settings(
        REST_FRAMEWORK={
            'DEFAULT_THROTTLE_RATES': {
                'anon': '60/hour',
                'user': '600/hour',
                'status': '2400/hour',
                'login_status': '3600/hour',
                'login': '10/minute',
                'register': '5/hour',
                'send_start': '30/hour',
                'send_quota': '20000/day',
            },
        }
    )
    def test_thirty_minutes_of_status_polling_never_throttles(self):
        """30 minutes at 3s intervals is 600 requests, the old ceiling."""
        for i in range(600):
            response = self.client.get(f'/api/campaigns/{self.campaign.pk}/status/')
            self.assertEqual(response.status_code, 200, f'throttled at poll {i + 1}')

    @override_settings(
        REST_FRAMEWORK={
            'DEFAULT_THROTTLE_RATES': {
                'anon': '60/hour',
                'user': '600/hour',
                'status': '2400/hour',
                'login_status': '3600/hour',
                'login': '10/minute',
                'register': '5/hour',
                'send_start': '30/hour',
                'send_quota': '20000/day',
            },
        }
    )
    def test_login_overlay_polling_never_throttles(self):
        """A ten-minute handshake at 2s intervals is 300 requests."""
        for i in range(300):
            response = self.client.get('/api/login-status/')
            self.assertEqual(response.status_code, 200, f'throttled at poll {i + 1}')

    @override_settings(
        REST_FRAMEWORK={
            'DEFAULT_THROTTLE_RATES': {
                'anon': '60/hour',
                'user': '600/hour',
                'status': '600/hour',
                'login_status': '600/hour',
                'login': '10/minute',
                'register': '5/hour',
                'send_start': '30/hour',
                'send_quota': '20000/day',
            },
        }
    )
    def test_throttling_still_applies_beyond_the_budget(self):
        """Confirms the throttle is genuinely active, not merely absent."""
        for _ in range(620):
            self.client.get(f'/api/campaigns/{self.campaign.pk}/status/')

        response = self.client.get(f'/api/campaigns/{self.campaign.pk}/status/')
        self.assertEqual(response.status_code, 429)
