"""
Django settings for the Bulk Sender backend.

Local development:
    Copy `backend/.env.example` to `backend/.env` and fill it in.
    Everything in there has a working dev default EXCEPT the secrets,
    which fall back to dev-only values that must never reach production.

Production (DEBUG=False) refuses to start unless real secrets are supplied
as environment variables — see the checks below. This is deliberate: the
old behaviour was to silently fall back to a committed key, which meant a
misconfigured deploy would boot "fine" and quietly encrypt real users'
phone numbers with a key that is public in this repository.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Load backend/.env before anything reads os.environ. python-dotenv does NOT
# overwrite variables that are already set, so a real environment variable
# always beats a value in the file — which is what makes this safe on hosts
# like Render, where the environment is injected rather than written to disk.
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / '.env')
except ImportError:  # pragma: no cover - dependency is in requirements.txt
    pass


# --- Small env helpers ---------------------------------------------------
# Settings used to read os.environ.get(...) inline in a dozen places, which
# made it impossible to answer "is this boolean or a string?" from the
# settings file alone. These three helpers centralise the parsing rules.

TRUTHY = {'1', 'true', 'yes', 'on'}
FALSY = {'0', 'false', 'no', 'off'}


def env_bool(name, default=False):
    """
    Reads a boolean environment variable, rejecting unrecognised values.

    The original version returned False for anything not in TRUTHY, which
    turned every typo into a silent False. `DEBUG=flase` therefore behaved as
    DEBUG=False, and the resulting failure pointed at CRYPTOGRAPHY_KEYS rather
    than at the variable actually misconfigured — you would have chased the
    wrong setting entirely.

    An unrecognised value now raises, naming the variable, what it received,
    and what it accepts.
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == '':
        return default

    normalized = raw.strip().lower()
    if normalized in TRUTHY:
        return True
    if normalized in FALSY:
        return False

    raise ImproperlyConfigured(
        f'{name}={raw!r} is not a valid boolean. '
        f'Use one of: {", ".join(sorted(TRUTHY | FALSY))}. '
        f'(Checked because a typo here is silent and hard to trace — for '
        f'instance DEBUG=flase reads as False and the app then fails on a '
        f'different setting entirely.)'
    )


def env_list(name, default=None):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == '':
        return list(default or [])
    return [item.strip() for item in raw.split(',') if item.strip()]


# --- Core security -------------------------------------------------------

# Defaults to True so a fresh clone runs with no setup. Production must set
# DEBUG=False explicitly; the checks further down then hard-fail on missing
# secrets rather than booting in a half-configured state.
DEBUG = env_bool('DEBUG', default=True)

# Dev-only fallback. Production MUST provide its own — see the guard below.
DEV_SECRET_KEY = 'django-insecure-pl#&)%1k&ilkw@mo$#uy9895-8%ct_^7=obj6_p^#63^zi*bdw'

SECRET_KEY = os.environ.get('SECRET_KEY') or DEV_SECRET_KEY

# The dev hostnames are allowed automatically when DEBUG is on. '*' is NOT
# the default any more: the previous fallback meant a production deploy that
# forgot to set ALLOWED_HOSTS accepted requests for any Host header, which
# is a header-injection/host-poisoning risk.
ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1', '[::1]'])

# --- Encryption keys -----------------------------------------------------
# Phone numbers and message bodies are encrypted at rest with Fernet (see
# sender/encryption.py). Keys are supplied as a comma-separated list, most
# recent first: the FIRST key encrypts, and EVERY key is tried when
# decrypting. That ordering is what makes rotation possible — you prepend a
# new key, re-save rows over time, then drop the old one from the end, with
# no window where existing data becomes unreadable.
#
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#
# Losing every key makes all encrypted data permanently unreadable. Back the
# list up somewhere real (a password manager), not in this repo.
DEV_CRYPTOGRAPHY_KEY = 'zH8f3sVv0kR5cQ2mB9pT1wL6xY4nJ7eD8uG3aF5oI2s='

# CRYPTOGRAPHY_KEYS is the current name; CRYPTOGRAPHY_KEY is still read so
# existing deployments don't break on upgrade.
_crypto_raw = os.environ.get('CRYPTOGRAPHY_KEYS') or os.environ.get('CRYPTOGRAPHY_KEY')
CRYPTOGRAPHY_KEYS = [key.strip() for key in (_crypto_raw or '').split(',') if key.strip()]

if not CRYPTOGRAPHY_KEYS:
    if DEBUG:
        CRYPTOGRAPHY_KEYS = [DEV_CRYPTOGRAPHY_KEY]
    else:
        raise ImproperlyConfigured(
            "CRYPTOGRAPHY_KEYS must be set when DEBUG=False. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )

if not DEBUG and DEV_CRYPTOGRAPHY_KEY in CRYPTOGRAPHY_KEYS:
    # This fallback was committed to the repository. Booting with it would
    # encrypt every real user's phone number under a publicly known key, so
    # treat it as a hard error outside development.
    raise ImproperlyConfigured(
        'CRYPTOGRAPHY_KEYS is still set to the development key that was '
        'committed to this repository. Generate a real key with: python -c '
        '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
    )

CRYPTOGRAPHY_KEYS = [key.encode() for key in CRYPTOGRAPHY_KEYS]

if not DEBUG and SECRET_KEY == DEV_SECRET_KEY:
    raise ImproperlyConfigured(
        'SECRET_KEY must be set to a real value when DEBUG=False. '
        'Generate one with: python -c "from django.core.management.utils import '
        'get_random_secret_key as g; print(g())"'
    )

if not DEBUG and '*' in ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS must not be '*' when DEBUG=False. Set it to your real hostnames."
    )


# --- Application definition ---------------------------------------------

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework.authtoken',
    'corsheaders',
    'sender',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'


# --- Database ------------------------------------------------------------

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        # Read as a single value, not a comma-separated list. env_list() was
        # used here originally, which would silently truncate a path
        # containing a comma to everything before it.
        'NAME': os.environ.get('DATABASE_PATH') or str(BASE_DIR / 'db.sqlite3'),
        # Sends run on background threads while HTTP requests are being served,
        # so a write lock can be held at the moment a read starts. Without a
        # timeout, SQLite raises "database is locked" immediately instead of
        # waiting, which surfaced as a failed campaign.
        'OPTIONS': {'timeout': int(os.environ.get('SQLITE_TIMEOUT_SECONDS', '20'))},
    }
}

# Campaign media is uploaded as multipart on the campaign-create request, and
# Django bounds the *whole* request body by DATA_UPLOAD_MAX_MEMORY_SIZE.
# The default (2.5MB) rejected the first photo anyone tried to attach, so it
# is raised here; per-file size is still enforced in sender/views.py.
MAX_MEDIA_BYTES = int(os.environ.get('MAX_MEDIA_BYTES', 25 * 1024 * 1024))
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_MEDIA_BYTES + 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 20_000
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024


# --- Password validation -------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# --- REST Framework ------------------------------------------------------

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    # Baseline limits. The auth endpoints use ScopedRateThrottle with their
    # own rates (see sender/throttles.py) — without these, /auth/login/ was
    # an unthrottled password-guessing oracle.
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': env_list('THROTTLE_ANON', default=['60/hour'])[0],
        'user': env_list('THROTTLE_USER', default=['600/hour'])[0],
        'login': env_list('THROTTLE_LOGIN', default=['10/minute'])[0],
        'register': env_list('THROTTLE_REGISTER', default=['5/hour'])[0],
        'send_start': env_list('THROTTLE_SEND_START', default=['30/hour'])[0],
        'send_quota': env_list('THROTTLE_SEND_QUOTA', default=['20000/day'])[0],
    },
    # Unused by design: the sender app has no browseable HTML/API-root needs,
    # and leaving it on made every 404 fall back to an HTML error page that
    # the frontend's `await response.json()` then blew up on.
    'EXCEPTION_HANDLER': 'sender.exceptions.api_exception_handler',
}


# --- Internationalization ------------------------------------------------

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# --- Static & media files ------------------------------------------------

STATIC_URL = 'static/'

# Campaign media is deliberately NOT served from here. MEDIA_URL used to be
# wired up with django.conf.urls.static.static() in DEBUG, which exposes
# every uploaded photo/video at a guessable, unauthenticated URL to anyone
# on the internet. It is now streamed through an ownership-checked DRF view
# (sender.views.serve_media_file) that requires a valid token.
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Where Playwright writes debug screenshots. Kept out of MEDIA_ROOT so
# screenshots of someone's logged-in WhatsApp session can never be served
# or mixed in with campaign media.
WHATSAPP_DEBUG_DIR = Path(os.environ.get('WHATSAPP_DEBUG_DIR', BASE_DIR / 'whatsapp_debug'))

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# --- CORS ----------------------------------------------------------------

CORS_ALLOWED_ORIGINS = env_list('CORS_ALLOWED_ORIGINS', default=['http://localhost:5173'])
CORS_URLS_REGEX = r'^/api/'


# --- Cache ----------------------------------------------------------------
# Throttle counters live in the cache, so the cache backend decides whether
# rate limits are actually global.
#
# LocMemCache is per-process. Under gunicorn with N workers, every limit above
# was effectively N times looser than configured, because each worker kept its
# own counters — and a password-guessing loop that gets 10 tries per worker
# rather than 10 total is not the limit anyone intended.
#
# Set REDIS_URL in production and all of them become shared:
#   REDIS_URL=redis://localhost:6379/0
#
# Falls back to a local file cache when Redis isn't configured, which at least
# survives worker restarts on a single box.

_redis_url = os.environ.get('REDIS_URL', '').strip()

if _redis_url:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': _redis_url,
            'KEY_PREFIX': os.environ.get('CACHE_KEY_PREFIX', 'bulk-sender'),
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
            'LOCATION': BASE_DIR / '.cache',
            # Touching is enough: throttle keys are created on write, and we
            # only ever need a counter that resets on process restart anyway.
            'TIMEOUT': 60 * 60 * 24,
        }
    }


# --- Stale campaign recovery ----------------------------------------------
# A send runs on a thread inside a web worker, so gunicorn recycling a worker
# or a reboot can kill it outright. Without a staleness timeout the campaign
# row stays 'running' forever and blocks that user from ever starting another
# send. See sender/reliability.py.
CAMPAIGN_STALE_AFTER_SECONDS = int(
    os.environ.get('CAMPAIGN_STALE_AFTER_SECONDS', str(45 * 60))
)


# --- Production security -------------------------------------------------

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = 'same-origin'
    SECURE_SSL_REDIRECT = env_bool('SECURE_SSL_REDIRECT', default=True)
    X_FRAME_OPTIONS = 'DENY'
    # Opt-in rather than on by default: HSTS is effectively irreversible, and
    # enabling it against a host that can't serve HTTPS would lock everyone
    # out. Set SECURE_HSTS_SECONDS=31536000 once HTTPS is confirmed working.
    SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '0'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
    SECURE_HSTS_PRELOAD = env_bool('SECURE_HSTS_PRELOAD', default=False)


# --- Logging -------------------------------------------------------------
# sender/whatsapp_automation.py used bare print() throughout, so everything
# it logged went to stdout in whatever format the process happened to be
# running under and was invisible to `manage.py runserver`'s error stream.
# Real logging means the send loop is diagnosable after the fact.

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': os.environ.get('LOG_LEVEL', 'INFO'),
    },
    'loggers': {
        'sender': {
            'handlers': ['console'],
            'level': os.environ.get('LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
    },
}