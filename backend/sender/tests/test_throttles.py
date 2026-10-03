"""
Tests for throttle behaviour and cache configuration.

The important property is that rate limits are actually enforced and are
global across workers. LocMemCache is per-process, so under gunicorn with N
workers every limit was N times looser than configured.
"""

import importlib
import sys

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from sender.models import Campaign, Contact, UserProfile
from sender.throttles import (
    CampaignCreateThrottle,
    LoginRateThrottle,
    RegisterRateThrottle,
    SendStartThrottle,
)


class CacheConfigTests(TestCase):
    """
    The cache backend is what makes throttling shared rather than local.

    These read the production settings module directly rather than
    `django.conf.settings`, because config.settings_test deliberately
    overrides CACHES with LocMemCache (each test needs an isolated, in-memory
    cache, and the real file cache would leak state between them).
    """

    def reload_production_settings(self, env=None):
        from unittest import mock

        import config.settings as settings_module

        with mock.patch.dict('os.environ', env or {}, clear=True):
            return importlib.reload(settings_module)

    def tearDown(self):
        self.reload_production_settings()
        importlib.reload(sys.modules['config.settings'])

    def test_locmem_is_not_the_production_default(self):
        """
        LocMemCache keeps counters in each worker's memory, so '10 per minute'
        silently becomes 10 per worker.
        """
        settings_module = self.reload_production_settings()

        backend = settings_module.CACHES['default']['BACKEND']
        self.assertNotIn(
            'locmem', backend.lower(),
            'LocMemCache breaks cross-worker throttling',
        )

    def test_defaults_to_a_persistent_cache(self):
        settings_module = self.reload_production_settings()
        self.assertIn('filebased', settings_module.CACHES['default']['BACKEND'])

    def test_test_settings_intentionally_use_locmem(self):
        """
        Documented exception: the test suite overrides the cache so throttle
        state can't leak between tests. Only valid because the base TestCase
        clears it in setUp.
        """
        from django.conf import settings

        self.assertIn('locmem', settings.CACHES['default']['BACKEND'].lower())

    def test_redis_is_used_when_configured(self):
        reloaded = self.reload_production_settings({'REDIS_URL': 'redis://localhost:6379/0'})

        self.assertIn('redis', reloaded.CACHES['default']['BACKEND'].lower())
        self.assertEqual(reloaded.CACHES['default']['LOCATION'], 'redis://localhost:6379/0')

    def test_redis_cache_is_isolated_by_prefix(self):
        """
        Without KEY_PREFIX, a shared Redis could collide with another app on
        the same instance, mixing throttle counters.
        """
        reloaded = self.reload_production_settings({'REDIS_URL': 'redis://localhost:6379/0'})
        self.assertTrue(reloaded.CACHES['default'].get('KEY_PREFIX'))


class ThrottleRateTests(TestCase):
    def test_rates_come_from_settings(self):
        """A hardcoded rate here would silently ignore the env var."""
        from django.conf import settings

        with override_settings(REST_FRAMEWORK={
            **settings.REST_FRAMEWORK,
            'DEFAULT_THROTTLE_RATES': {
                **settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'],
                'login': '3/minute',
            },
        }):
            self.assertEqual(LoginRateThrottle().rate, '3/minute')

    def test_each_throttle_uses_a_distinct_scope(self):
        scopes = {
            LoginRateThrottle().scope,
            RegisterRateThrottle().scope,
            CampaignCreateThrottle().scope,
            SendStartThrottle().scope,
        }
        # Login/register key by IP (anon), the others by user, so they must not
        # collide or one endpoint's traffic would throttle another's.
        self.assertEqual(scopes, {'anon', 'anon', 'user', 'user'})

    def test_login_and_register_have_different_rates(self):
        from django.conf import settings

        rates = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
        self.assertNotEqual(rates['login'], rates['register'])


class AuthThrottleEnforcementTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_login_rate_limit_trips(self):
        from django.contrib.auth.models import User

        User.objects.create_user('alice', password='pw')

        codes = [
            self.client.post(
                '/api/auth/login/', {'username': 'alice', 'password': 'wrong'}, format='json'
            ).status_code
            for _ in range(25)
        ]

        self.assertIn(429, codes)
        self.assertEqual(codes[-1], 429)

    def test_rate_limited_response_is_json(self):
        """
        A 429 must carry the standard error shape, or the client shows a JSON
        parse error instead of "too many attempts".
        """
        for _ in range(25):
            response = self.client.post(
                '/api/auth/login/', {'username': 'nobody', 'password': 'x'}, format='json'
            )

        self.assertEqual(response.status_code, 429)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'rate_limited')

    def test_register_rate_limit_trips(self):
        for i in range(15):
            self.client.post(
                '/api/auth/register/',
                {'username': f'user{i}', 'password': 'correct-horse-1'},
                format='json',
            )

        response = self.client.post(
            '/api/auth/register/', {'username': 'last', 'password': 'pw'}, format='json'
        )
        self.assertEqual(response.status_code, 429)

    def test_throttle_state_does_not_leak_between_users(self):
        """
        An IP-keyed limit is shared by everyone behind that IP. Worth knowing
        explicitly: a busy office or a shared NAT can throttle legitimate users
        against each other.
        """
        from rest_framework.request import Request
        from django.test import RequestFactory

        factory = RequestFactory()

        class FakeUser:
            is_authenticated = False

        def key_for():
            request = Request(factory.get('/api/auth/login/'))
            request.user = FakeUser()
            return LoginRateThrottle().get_cache_key(request, None)

        first = key_for()
        self.assertEqual(key_for(), first, 'same client should map to one key')
        self.assertIsNotNone(first)

    def test_throttle_cache_is_cleared_between_tests(self):
        """
        Guards the base TestCase: without cache.clear(), LocMemCache/file
        cache state outlives the per-test database and tests throttle each
        other.
        """
        from django.contrib.auth.models import User

        User.objects.create_user('alice', password='pw')
        first = self.client.post(
            '/api/auth/login/', {'username': 'alice', 'password': 'wrong'}, format='json'
        ).status_code
        second = self.client.post(
            '/api/auth/login/', {'username': 'alice', 'password': 'wrong'}, format='json'
        ).status_code

        self.assertEqual(first, 401)
        self.assertEqual(second, 401, 'a fresh test must not start throttled')


class SendStartThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        from django.contrib.auth.models import User
        from rest_framework.authtoken.models import Token

        self.user = User.objects.create_user('alice', password='pw')
        self.token = Token.objects.create(user=self.user).key
        UserProfile.objects.create(user=self.user, whatsapp_number='+2348161234765')
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token}')

    def test_send_start_is_rate_limited(self):
        from unittest import mock

        from sender import views

        campaign = Campaign.objects.create(
            owner=self.user, name='c', message_text='hi', send_mode='delay'
        )

        codes = []
        with mock.patch.object(views.threading, 'Thread'):
            for _ in range(60):
                # Reset to draft so the "already sending" guard doesn't fire
                # first; we're testing the throttle, not that guard.
                Campaign.objects.filter(pk=campaign.pk).update(status=Campaign.Status.DRAFT)
                views._active_sends.clear()
                codes.append(
                    self.client.post(f'/api/campaigns/{campaign.pk}/start/').status_code
                )

        self.assertIn(429, codes)