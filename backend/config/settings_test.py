"""
Settings for running the test suite.

Run with:

    python manage.py test --settings=config.settings_test

The only meaningful differences are the password hasher and the environment
isolation below. Django's default PBKDF2 (~1M iterations) costs roughly half a
second per hash, and this suite creates users constantly — that alone added
minutes of wall time for no extra coverage. MD5 here is a deliberate test-only
choice and must never be used outside tests.
"""

import os

# --- Hermetic environment --------------------------------------------------
# These are pinned BEFORE settings.py is imported, and unconditionally, so the
# suite does not inherit whatever the developer's shell happens to export.
#
# This is not hypothetical. settings.env_bool() rejects unrecognised booleans
# (that strictness is deliberate — it is what catches typos like DEBUG=flase),
# which means one stray exported variable can make the whole suite unrunnable.
# A shell exporting DEBUG as captured test output is a real instance: it
# arrived here as DEBUG='-LH:*, LH:*:error' and every invocation died at
# import with ImproperlyConfigured before a single test ran.
#
# It cuts both ways. A developer with DEBUG=false exported globally would
# otherwise trip the production guards (SECRET_KEY, CRYPTOGRAPHY_KEYS,
# ALLOWED_HOSTS) and again fail to boot. And WHATSAPP_DISABLE_AUTOMATION —
# which exists for smoke.py — silently rewrites the send path, so a globally
# exported one turns the automation tests into assertions about a stub.
#
# Tests that care about DEBUG=False or about the production guards assert that
# behaviour explicitly: sender/tests/test_settings_env.py reloads settings
# under its own controlled environment. Pinning here does not weaken those
# tests, it stops the ambient shell from deciding whether the suite can run.
os.environ['DEBUG'] = 'true'
os.environ.setdefault('SECRET_KEY', 'test-suite-only-not-a-real-secret-key')
# A real Fernet key: 32 url-safe base64 bytes. It has to be genuinely valid,
# not just present — sender/encryption.py builds a Fernet per key at import,
# so a malformed placeholder fails every test that touches an encrypted field.
os.environ.setdefault(
    'CRYPTOGRAPHY_KEYS', 'cB1gEHpzTDkPalZNBZ7uvLOw8uSyMk6oIM29w5d3q1k='
)
os.environ['WHATSAPP_DISABLE_AUTOMATION'] = 'false'

from .settings import *  # noqa: F401,F403,E402

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

# Throttle state must not leak between tests via the default LocMemCache,
# and the send quota relies on the database, which is reset per test anyway.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'bulk-sender-tests',
    },
}

# Keep large-upload tests from allocating tens of megabytes. Individual
# tests override this when they need a specific ceiling.
MAX_MEDIA_BYTES = 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
