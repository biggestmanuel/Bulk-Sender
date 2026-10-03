"""
Settings for running the test suite.

Run with:

    python manage.py test --settings=config.settings_test

The only meaningful difference is the password hasher. Django's default
PBKDF2 (~1M iterations) costs roughly half a second per hash, and this
suite creates users constantly — that alone added minutes of wall time for
no extra coverage. MD5 here is a deliberate test-only choice and must never
be used outside tests.
"""

from .settings import *  # noqa: F401,F403

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