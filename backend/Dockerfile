# Backend image for the WhatsApp bulk sender.
#
# The important constraint: the automation runs a REAL browser against
# web.whatsapp.com, headed, because WhatsApp is unreliable about accepting the
# linked-device handshake from a headless Chromium. That means:
#
#   * a display server (Xvfb), not a bare container
#   * a persistent, writable volume for backend/whatsapp_session
#   * NOT the free tier of a typical PaaS, whose filesystems are ephemeral
#     (every deploy would wipe the linked WhatsApp session) and which have no
#     display
#
# Run it with the session directory on a named volume:
#
#   docker build -t bulk-sender backend
#   docker volume create bulk-sender-sessions
#   docker run -d --name bulk-sender \
#     -p 8000:8000 \
#     -v bulk-sender-sessions:/app/whatsapp_session \
#     --env-file .env \
#     bulk-sender
#
# See also docker-compose.yml at the repository root.

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    WHATSAPP_SESSION_DIR=/app/whatsapp_session \
    WHATSAPP_DEBUG_DIR=/app/whatsapp_debug \
    MEDIA_ROOT=/app/media \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Playwright's browser dependencies, Xvfb for a display, and fonts (WhatsApp
# Web renders text through the system font stack; a bare slim image has
# almost none, which shows up as blank labels and can break its own selectors).
RUN apt-get update && apt-get install --no-install-recommends -y \
        xvfb \
        ca-certificates \
        curl \
        fonts-liberation \
        fonts-noto-color-emoji \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libglib2.0-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libx11-6 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Installs the browser Playwright drives. --with-deps is redundant with the
# apt list above but keeps this correct if the browser's own deps change.
#
# PLAYWRIGHT_BROWSERS_PATH is set to a shared path rather than left at the
# default ~/.cache/ms-playwright. The default resolves to /root/.cache while
# this runs as root, but the container later drops to appuser, whose home is
# /home/appuser — so the browser would be installed somewhere the app cannot
# read, and the send would fail at launch with a bare "executable doesn't exist".
RUN playwright install --with-deps chromium

COPY . .

# Session and media directories are volumes; create them so the app can write
# before the mount exists.
RUN mkdir -p "$WHATSAPP_SESSION_DIR" "$WHATSAPP_DEBUG_DIR" "$MEDIA_ROOT" \
    && useradd --create-home --uid 1000 appuser

# Entry point wraps gunicorn in Xvfb so Chrome has a display to draw on.
#
# Copied and made executable BEFORE dropping privileges. COPY leaves the file
# owned by root, so a `RUN chmod` after `USER appuser` fails with "Operation
# not permitted" — which is exactly how this image failed to build until it
# was reordered.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/login-status/ -o /dev/null \
        || [ "$?" = "22" ]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--threads", "4", \
     "--timeout", "120", \
     "--access-logfile", "-"]
