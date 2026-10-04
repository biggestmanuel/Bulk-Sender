"""
Tests for env parsing in settings.py.

The original env_bool returned False for anything not in TRUTHY, so every
typo became a silent False. `DEBUG=flase` then behaved as DEBUG=False and
the resulting crash blamed CRYPTOGRAPHY_KEYS — pointing you at the wrong
setting entirely. These tests pin the strict behaviour.
"""

import importlib
import os
import sys
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase


class EnvBoolTests(SimpleTestCase):
    def reload(self, **env):
        import config.settings as settings_module

        with mock.patch.dict('os.environ', env, clear=True):
            return importlib.reload(settings_module)

    def tearDown(self):
        importlib.reload(sys.modules['config.settings'])

    def test_accepts_truthy_values(self):
        settings = self.reload(DEBUG='true', SECRET_KEY='x')
        for value in ('1', 'true', 'TRUE', 'yes', 'on', ' On '):
            settings = self.reload(DEBUG=value, SECRET_KEY='x')
            self.assertTrue(settings.DEBUG, value)

    def test_accepts_falsy_values(self):
        for value in ('0', 'false', 'FALSE', 'no', 'off'):
            settings = self.reload(
                DEBUG=value,
                SECRET_KEY='real',
                CRYPTOGRAPHY_KEYS='4Y5vJ8XqP2mN9sR3wT6bK1cD7fH0gL4aE2uO5iQ8yZ0=',
                ALLOWED_HOSTS='example.com',
            )
            self.assertFalse(settings.DEBUG, value)

    def test_defaults_when_unset(self):
        settings = self.reload(SECRET_KEY='x')
        self.assertTrue(settings.DEBUG, 'dev default should stay True')

    def test_defaults_when_empty_string(self):
        settings = self.reload(DEBUG='', SECRET_KEY='x')
        self.assertTrue(settings.DEBUG, 'empty should behave like unset')

    # --- The bug this fixes ----------------------------------------------

    def test_typo_does_not_silently_become_false(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            self.reload(DEBUG='flase')

        message = str(ctx.exception)
        self.assertIn('DEBUG', message)
        self.assertIn('flase', message)
        self.assertIn('not a valid boolean', message)

    def test_error_names_the_accepted_values(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            self.reload(DEBUG='maybe')

        message = str(ctx.exception)
        for expected in ('true', 'false', 'yes', 'no', 'on', 'off'):
            self.assertIn(expected, message)

    def test_shell_leak_into_debug_is_caught(self):
        """
        A concrete real-world instance: a shell that exports DEBUG as captured
        test output. Previously this read as False and the app failed on
        CRYPTOGRAPHY_KEYS, sending you to look at the wrong setting.
        """
        with self.assertRaises(ImproperlyConfigured) as ctx:
            self.reload(DEBUG='-LH:*, LH:*:error')

        self.assertIn('DEBUG', str(ctx.exception))

    def test_typo_error_does_not_blame_cryptography_keys(self):
        """
        The original failure mode: the real cause was DEBUG, but the message
        talked about encryption keys.
        """
        with self.assertRaises(ImproperlyConfigured) as ctx:
            self.reload(DEBUG='flase', SECRET_KEY='real', ALLOWED_HOSTS='example.com')

        self.assertNotIn('CRYPTOGRAPHY_KEYS', str(ctx.exception))

    def test_other_boolean_flags_are_strict_too(self):
        """
        The same silent inversion applied to every boolean flag, so a typo'd
        SECURE_SSL_REDIRECT was equally invisible.

        Note these are only read when DEBUG=False, so the test has to supply
        valid secrets to reach them — with DEBUG=True the flag is never
        consulted and there is nothing to reject.
        """
        import config.settings as settings_module

        with mock.patch.dict('os.environ', {
            'DEBUG': 'false',
            'SECRET_KEY': 'kJ8vQ2mN9sR3wT6bK1cD7fH0gL4aE2uO5iQ8yZ0xP3wT6bK1cD7fH0gL4aE2uO5iQ8yZ0',
            'CRYPTOGRAPHY_KEYS': '4Y5vJ8XqP2mN9sR3wT6bK1cD7fH0gL4aE2uO5iQ8yZ0=',
            'ALLOWED_HOSTS': 'example.com',
            'SECURE_SSL_REDIRECT': 'yess',
        }, clear=True):
            with self.assertRaises(ImproperlyConfigured) as ctx:
                importlib.reload(settings_module)

        self.assertIn('SECURE_SSL_REDIRECT', str(ctx.exception))

        importlib.reload(sys.modules['config.settings'])

    def test_quoted_value_is_not_silently_accepted(self):
        """
        A value wrapped in quotes is a very common copy-paste mistake and
        would otherwise read as False.
        """
        with self.assertRaises(ImproperlyConfigured):
            self.reload(DEBUG='"true"')


class EnvListTests(SimpleTestCase):
    def reload(self, **env):
        import config.settings as settings_module

        with mock.patch.dict('os.environ', env, clear=True):
            return importlib.reload(settings_module)

    def tearDown(self):
        importlib.reload(sys.modules['config.settings'])

    def test_splits_and_strips(self):
        settings = self.reload(ALLOWED_HOSTS=' a.com , b.com ,c.com ')
        self.assertEqual(settings.ALLOWED_HOSTS, ['a.com', 'b.com', 'c.com'])

    def test_drops_empty_entries(self):
        settings = self.reload(ALLOWED_HOSTS='a.com,,b.com,')
        self.assertEqual(settings.ALLOWED_HOSTS, ['a.com', 'b.com'])

    def test_falls_back_to_default(self):
        settings = self.reload()
        self.assertIn('localhost', settings.ALLOWED_HOSTS)
        self.assertNotIn('*', settings.ALLOWED_HOSTS)

    def test_does_not_mutate_the_default_list(self):
        """
        `list(default or [])` copies, so a caller's list can't be modified —
        but only if the default itself isn't shared. This pins that.
        """
        first = self.reload()
        first.ALLOWED_HOSTS.append('mutated.example.com')
        second = self.reload()
        self.assertNotIn('mutated.example.com', second.ALLOWED_HOSTS)


class StrictnessDoesNotBreakProductionConfig(SimpleTestCase):
    """Valid production settings must still load with strict parsing in place."""

    def test_valid_production_environment_loads(self):
        import config.settings as settings_module

        with mock.patch.dict('os.environ', {
            'DEBUG': 'false',
            'SECRET_KEY': 'kJ8vQ2mN9sR3wT6bK1cD7fH0gL4aE2uO5iQ8yZ0xP3wT6bK1cD7fH0gL4aE2uO5iQ8yZ0',
            'CRYPTOGRAPHY_KEYS': '4Y5vJ8XqP2mN9sR3wT6bK1cD7fH0gL4aE2uO5iQ8yZ0=',
            'ALLOWED_HOSTS': 'bulk-sender.example.com',
            'SECURE_SSL_REDIRECT': 'true',
        }, clear=True):
            reloaded = importlib.reload(settings_module)

        self.assertFalse(reloaded.DEBUG)
        self.assertTrue(reloaded.SECURE_SSL_REDIRECT)

        importlib.reload(sys.modules['config.settings'])


class TestSettingsAreHermetic(SimpleTestCase):
    """
    settings_test must not depend on the developer's shell.

    Strict parsing (above) means a single stray exported variable can make the
    entire suite unrunnable: DEBUG='-LH:*, LH:*:error' from a shell that
    captured test output raised ImproperlyConfigured at import, before a single
    test ran. settings_test pins the variables it depends on before importing
    settings, so this checks that from a clean subprocess — in-process the
    module is already imported, so the assertion would be vacuous.
    """

    HOSTILE_ENVIRON = {
        # A boolean flag that is not a boolean.
        'DEBUG': '-LH:*, LH:*:error',
        # Production posture without the secrets it demands.
        'SECRET_KEY': '',
        'CRYPTOGRAPHY_KEYS': '',
        # The smoke-test escape hatch, exported by accident.
        'WHATSAPP_DISABLE_AUTOMATION': 'true',
    }

    def _import_settings_test(self, extra_env=None):
        """
        Runs `import config.settings_test` in a subprocess.

        Returns (returncode, combined output). A subprocess is the only honest
        way to test import-time behaviour.
        """
        import subprocess
        import sys
        from pathlib import Path

        # .../backend/sender/tests/test_settings_env.py -> .../backend
        backend_dir = Path(__file__).resolve().parents[2]

        env = dict(os.environ)
        env.pop('DEBUG', None)
        env.update(self.HOSTILE_ENVIRON)
        if extra_env:
            env.update(extra_env)

        completed = subprocess.run(
            [sys.executable, '-c', 'import config.settings_test as s; print("DEBUG", s.DEBUG)'],
            cwd=str(backend_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return completed.returncode, completed.stdout + completed.stderr

    def test_boots_despite_a_hostile_environment(self):
        code, output = self._import_settings_test()

        self.assertEqual(
            code, 0,
            f'settings_test failed to import under a hostile environment:\n{output}',
        )
        self.assertIn('DEBUG True', output)

    def test_sends_are_not_silently_disabled(self):
        """
        A globally exported WHATSAPP_DISABLE_AUTOMATION would otherwise turn
        the automation tests into assertions about a stub that marks every
        contact sent without contacting WhatsApp.
        """
        import config.settings_test as settings_test

        self.assertEqual(os.environ['WHATSAPP_DISABLE_AUTOMATION'], 'false')

        from sender import whatsapp_automation as wa

        with mock.patch.dict('os.environ', {}, clear=False):
            self.assertFalse(wa._env_flag('WHATSAPP_DISABLE_AUTOMATION', 'false'))