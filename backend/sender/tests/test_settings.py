"""
Tests for the security-critical settings.

settings.py is mostly declarative, but a few of its rules exist specifically
to stop a misconfigured deploy from booting in an unsafe state, and those
rules are only worth anything if they're actually exercised. These tests
re-import the settings module with patched environments to verify each guard
fires.
"""

import importlib
import os
import sys
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

SETTINGS_MODULE = 'config.settings'

# The dev fallback values from the original settings.py, which were committed
# to the repository. Production must reject both.
DEV_CRYPTOGRAPHY_KEY = 'zH8f3sVv0kR5cQ2mB9pT1wL6xY4nJ7eD8uG3aF5oI2s='
DEV_SECRET_KEY = 'django-insecure-pl#&)%1k&ilkw@mo$#uy9895-8%ct_^7=obj6_p^#63^zi*bdw'


def reload_settings(env):
    """Re-imports config.settings with `env` as the environment."""
    import config.settings as settings_module

    with mock.patch.dict(os.environ, env, clear=True):
        return importlib.reload(settings_module)


class ProductionGuardTests(SimpleTestCase):
    """
    With DEBUG=False the app must refuse to start unless real secrets exist.

    The original code fell back to a committed key, which meant a deploy that
    forgot to set an env var booted "successfully" and encrypted every user's
    phone number with a key published in this repository.
    """

    def tearDown(self):
        # Leave the settings module in a usable state for other tests.
        reload_settings({})
        importlib.reload(sys.modules[SETTINGS_MODULE])

    def test_refuses_without_cryptography_keys(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            reload_settings({'DEBUG': 'False'})
        self.assertIn('CRYPTOGRAPHY_KEYS', str(ctx.exception))

    def test_refuses_the_committed_dev_crypto_key_in_production(self):
        """
        The dev fallback key was committed in the original settings.py, so a
        production deploy that forgot to set CRYPTOGRAPHY_KEYS would have
        encrypted every real phone number with a key in this public repo.
        """
        with self.assertRaises(ImproperlyConfigured) as ctx:
            reload_settings({
                'DEBUG': 'False',
                'SECRET_KEY': 'a-real-secret',
                'CRYPTOGRAPHY_KEYS': DEV_CRYPTOGRAPHY_KEY,
            })
        self.assertIn('CRYPTOGRAPHY_KEYS', str(ctx.exception))

    def test_refuses_the_committed_dev_secret_key(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            reload_settings({
                'DEBUG': 'False',
                'SECRET_KEY': DEV_SECRET_KEY,
                'CRYPTOGRAPHY_KEYS': '4Y5vJ8XqP2mN9sR3wT6bK1cD7fH0gL4aE2uO5iQ8yZ0=',
            })
        self.assertIn('SECRET_KEY', str(ctx.exception))

    def test_refuses_wildcard_allowed_hosts_in_production(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            reload_settings({
                'DEBUG': 'False',
                'SECRET_KEY': 'a-real-secret',
                'CRYPTOGRAPHY_KEYS': '4Y5vJ8XqP2mN9sR3wT6bK1cD7fH0gL4aE2uO5iQ8yZ0=',
                'ALLOWED_HOSTS': '*',
            })
        self.assertIn('ALLOWED_HOSTS', str(ctx.exception))

    def test_accepts_a_fully_configured_production_environment(self):
        settings = reload_settings({
            'DEBUG': 'False',
            'SECRET_KEY': 'a-real-secret',
            'CRYPTOGRAPHY_KEYS': '4Y5vJ8XqP2mN9sR3wT6bK1cD7fH0gL4aE2uO5iQ8yZ0=',
            'ALLOWED_HOSTS': 'example.com',
        })
        self.assertFalse(settings.DEBUG)
        self.assertEqual(settings.ALLOWED_HOSTS, ['example.com'])
        self.assertTrue(settings.SESSION_COOKIE_SECURE)
        self.assertTrue(settings.CSRF_COOKIE_SECURE)
        self.assertEqual(settings.X_FRAME_OPTIONS, 'DENY')

    def test_legacy_single_key_env_var_still_works(self):
        """Deployments upgrading from the old CRYPTOGRAPHY_KEY name."""
        settings = reload_settings({
            'DEBUG': 'False',
            'SECRET_KEY': 'a-real-secret',
            'CRYPTOGRAPHY_KEYS': '',
            'CRYPTOGRAPHY_KEY': '4Y5vJ8XqP2mN9sR3wT6bK1cD7fH0gL4aE2uO5iQ8yZ0=',
            'ALLOWED_HOSTS': 'example.com',
        })
        self.assertEqual(len(settings.CRYPTOGRAPHY_KEYS), 1)


class DevDefaultsTests(SimpleTestCase):
    def tearDown(self):
        reload_settings({})
        importlib.reload(sys.modules[SETTINGS_MODULE])

    def test_debug_defaults_on_with_no_env(self):
        settings = reload_settings({})
        self.assertTrue(settings.DEBUG)

    def test_allowed_hosts_does_not_default_to_wildcard(self):
        """
        The old default was ['*'], which accepted any Host header even outside
        DEBUG when the env var was unset.
        """
        settings = reload_settings({})
        self.assertNotIn('*', settings.ALLOWED_HOSTS)
        self.assertIn('localhost', settings.ALLOWED_HOSTS)

    def test_parses_boolean_and_list_env_vars(self):
        settings = reload_settings({
            'DEBUG': 'false',
            'SECRET_KEY': 'a-real-secret',
            'CRYPTOGRAPHY_KEYS': '4Y5vJ8XqP2mN9sR3wT6bK1cD7fH0gL4aE2uO5iQ8yZ0=',
            'ALLOWED_HOSTS': 'a.com, b.com ,c.com',
            'CORS_ALLOWED_ORIGINS': 'https://a.com,https://b.com',
        })
        self.assertEqual(settings.ALLOWED_HOSTS, ['a.com', 'b.com', 'c.com'])
        self.assertEqual(settings.CORS_ALLOWED_ORIGINS, ['https://a.com', 'https://b.com'])

    def test_multiple_crypto_keys_are_ordered_newest_first(self):
        settings = reload_settings({
            'DEBUG': 'False',
            'SECRET_KEY': 'a-real-secret',
            'ALLOWED_HOSTS': 'example.com',
            'CRYPTOGRAPHY_KEYS': 'aaaa,bbbb,cccc',
        })
        # Order is preserved exactly, because it determines which key encrypts.
        self.assertEqual(settings.CRYPTOGRAPHY_KEYS, [b'aaaa', b'bbbb', b'cccc'])


class MediaExposureTests(SimpleTestCase):
    """
    MEDIA_ROOT must not be routed at the root level.

    It used to be wired up with static(), which made every uploaded campaign
    photo and video world-readable at a guessable URL with no
    authentication. Campaign media now goes through an ownership-checked
    endpoint under /api/.
    """

    def test_root_urlconf_has_no_media_route(self):
        from django.urls import get_resolver

        prefixes = [str(p.pattern) for p in get_resolver().url_patterns]
        self.assertFalse(
            [p for p in prefixes if p.startswith('/media')],
            f'MEDIA_URL must not be routed; found {prefixes}',
        )

    def test_media_is_served_only_through_the_authenticated_api(self):
        from django.urls import reverse

        url = reverse('serve_media_file', args=[1, 1])
        self.assertEqual(url, '/api/campaigns/1/media/1/')

    def test_plain_media_path_is_not_resolvable(self):
        from django.urls import Resolver404, resolve

        with self.assertRaises(Resolver404):
            resolve('/media/campaign_media/secret.png')