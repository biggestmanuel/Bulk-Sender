# Bulk Sender

A self-hosted WhatsApp bulk-messaging tool. Upload a CSV of contacts, write a
message with a `{name}` placeholder, and the backend drives WhatsApp Web in a
real browser to send it to every contact.

Django + DRF backend, React + Vite frontend.

---

## ⚠️ Read this first

**This sends unsolicited bulk messages through an unofficial automation
layer.** WhatsApp has no public send API, so this drives the WhatsApp Web UI
with Playwright. That means:

- **Your number can be banned.** WhatsApp prohibits bulk messaging and
  automated sending. No delay setting, header spoofing, or pacing change that.
  The "delay" mode here makes detection *less trivial*, not safe. If the
  number matters to you, don't use this on it.
- **Recipients must have opted in.** Sending marketing messages to people
  who didn't ask for them is unlawful in many jurisdictions (GDPR, CAN-SPAM,
  Nigeria's NDPA, and others) regardless of the technical setup.
- **You can get your contact list banned too**, which affects your personal
  WhatsApp account.

The pacing logic and the honest comments around it exist because pretending
this is undetectable would be the more harmful failure.

---

## How it works

1. Register, then set the WhatsApp number your account will link with.
2. Upload a CSV with a header row. Map which column is the name and which is
   the phone number.
3. Fix any rows flagged invalid or missing a country code.
4. Write a message (`{name}` is replaced per contact), optionally attach media,
   and choose a send mode.
5. Start the send. The backend launches a browser, logs into WhatsApp Web, and
   asks you to confirm the link on your phone.
6. Progress polls every 3s and stops on its own when the campaign finishes.

### The login handshake

There's no QR scanner. The backend opens WhatsApp Web, selects your country,
submits your number, and scrapes the 8-character code WhatsApp displays. That
code appears in the browser overlay — you type it into your phone under
**Settings → Linked devices → Link a device → Link with phone number
instead**.

Login state lives in the database, not files in `MEDIA_ROOT`, so it works
across gunicorn workers and survives a redeploy.

Each user gets their own persistent browser profile, so the link persists
between campaigns and two people can send at the same time without colliding.
If a session turns out to be expired, one automatic retry wipes the profile
and re-links. You can force this manually with
`POST /api/whatsapp-session/reset/`.

---

## Setup

### Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # macOS/Linux

pip install -r requirements.txt
python -m playwright install chromium
```

Copy `backend/.env.example` to `backend/.env` — it's loaded automatically.
Then:

```bash
python manage.py migrate
python manage.py runserver
```

API is on `http://127.0.0.1:8000/api`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

App is on `http://localhost:5173`.

No CORS setup is needed locally: the Vite dev server proxies `/api` to
Django, so the browser only ever talks to one origin.

---

## Configuration

Everything is optional in development. **With `DEBUG=False` the app refuses to
start unless `SECRET_KEY` and `CRYPTOGRAPHY_KEYS` are real values** — it won't
silently fall back to the development keys, which were once committed here.

### Encryption

Phone numbers and message bodies are encrypted at rest with Fernet
(`backend/sender/encryption.py`).

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`CRYPTOGRAPHY_KEYS` is a comma-separated list, **newest first**. The first key
encrypts; all of them are tried when decrypting. That's what makes rotation
possible — prepend a new key, then drop the old one later, with no window
where existing data becomes unreadable.

Losing every key makes all stored data permanently unreadable. Back the list
up in a password manager.

### Other settings

| Variable | Default | Notes |
|---|---|---|
| `DEBUG` | `True` | Must be `False` in production |
| `ALLOWED_HOSTS` | localhost only | Comma-separated; `*` is rejected when `DEBUG=False` |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173` | Frontend origin(s) |
| `MAX_MEDIA_BYTES` | 25MB | Per uploaded attachment |
| `THROTTLE_SEND_QUOTA` | `20000/day` | Contacts one user may queue per day |
| `THROTTLE_LOGIN` | `10/minute` | Per IP |
| `REDIS_URL` | unset | Set for shared rate limits; see below |
| `CAMPAIGN_STALE_AFTER_SECONDS` | `2700` | When an unresponsive send is presumed dead |
| `WHATSAPP_HEADLESS` | `false` | See "Headless vs headed" |
| `WHATSAPP_DEBUG` | `false` | Writes screenshots of the automation steps |
| `SECURE_HSTS_SECONDS` | `0` | Raise once HTTPS is confirmed working |

### Rate limits need a shared cache

Throttle counters are stored in Django's cache. The default here is a local
**file** cache, which is shared between workers on one box but resets on
restart. `LocMemCache` is deliberately *not* the default — it's per-process,
so under gunicorn with N workers every limit silently becomes N times looser
than configured.

For genuinely shared limits (or multiple hosts), set `REDIS_URL`.

### Headless vs headed

The automation defaults to **headed** mode. WhatsApp Web is unreliable about
accepting the linked-device handshake from a headless browser, and the phone
side of the link needs a person anyway. It also prefers a locally installed
Chrome/Edge/Brave over Playwright's bundled Chromium for the same reason.

This means **this does not run on a typical PaaS host.** There's no display,
and the Chromium profile lives on an ephemeral filesystem that gets wiped on
every deploy. Run it on a machine with a desktop session, or over a VNC/RDP
session on a long-lived box.

`WHATSAPP_DEBUG=true` writes screenshots to `backend/whatsapp_debug/` — a
separate directory, never `MEDIA_ROOT`, because they capture a logged-in
WhatsApp session.

### Running it in a container

A container has no display either, so the backend image runs gunicorn under
**Xvfb**, which gives Chrome a virtual screen. `backend/Dockerfile` and
`backend/docker/entrypoint.sh` do this; the entrypoint waits for the X socket
to appear rather than sleeping blindly.

The one volume that matters is the WhatsApp session:

```bash
docker build -t bulk-sender backend
docker volume create bulk-sender-sessions
docker run -d -p 8000:8000 \
  -v bulk-sender-sessions:/app/whatsapp_session \
  --env-file .env \
  bulk-sender
```

Without a persistent volume for that directory, every restart forces a fresh
phone-link handshake. `docker-compose.yml` wires it up as a named volume, with
Postgres and Redis as opt-in profiles.

---

## API

All endpoints are under `/api`. Auth is a DRF token in the `Authorization`
header.

| Method | Path | Notes |
|---|---|---|
| `POST` | `auth/register/` | Rate limited per IP |
| `POST` | `auth/login/` | Rate limited per IP |
| `GET`/`PUT` | `profile/whatsapp-number/` | Validated and normalized |
| `GET` | `campaigns/` | Paginated history with progress counts |
| `POST` | `campaigns/create/` | Multipart; rejects the whole request if any number is unusable |
| `GET` | `campaigns/<id>/status/` | Owner only |
| `POST` | `campaigns/<id>/start/` | One send per campaign, one per user |
| `POST` | `campaigns/<id>/retry/` | Returns failed contacts to pending |
| `DELETE` | `campaigns/<id>/` | Removes the campaign and its files |
| `GET` | `campaigns/<id>/media/<file_id>/` | Owner only |
| `GET` | `login-status/` | Current handshake state |
| `POST` | `whatsapp-session/reset/` | Force a re-link |

### Retrying a failed campaign

`POST /api/campaigns/<id>/retry/` flips `failed` contacts back to `pending`
and returns the count. It deliberately does **not** restart the send — call
`/start/` separately, so the usual guards (already sending, daily quota) still
apply and you can see what happened. Contacts that were already `sent` are
never touched, so a retry can't re-send to people who already got the message.

**A partially-delivered campaign is never re-sent automatically.** If the
browser dies part-way through, the send gives up rather than starting the list
again, because re-running it would deliver a second message to everyone
already reached. The session is only re-linked and retried when nothing was
attempted at all, which is the login-failure case. When this happens the
campaign ends as `failed` with the delivered contacts left `sent`, so a manual
`/retry/` picks up only the ones that were genuinely missed.

A dead browser is detected rather than waited out. Each failed send is
followed by a cheap liveness probe, and once the browser is gone the send
stops immediately instead of working through the rest of the list. That
matters because a dead browser fails *every* remaining contact, and each one
costs the full navigation timeout — minutes per contact, and the campaign
would sit there looking busy for hours. The log line naming the count is:

```
Browser died for user 5 after 2 of 500 contact(s) were attempted (1 sent);
settling the remaining 498 rather than failing each one slowly
```

### Recovering from a dead worker

A send runs on a thread inside a web worker. If gunicorn recycles that worker,
the box reboots, or the process is killed, the campaign row stays `running`
with nothing behind it. Left alone that blocks that user from starting anything
again, permanently.

The worker writes a heartbeat on every contact. Anything quiet for longer than
`CAMPAIGN_STALE_AFTER_SECONDS` is presumed dead, marked failed, and its
unsent contacts settled. This runs automatically before each send attempt; for
a fully unattended deployment, run the reaper from cron:

```bash
python manage.py reap_stale_campaigns --dry-run   # report
python manage.py reap_stale_campaigns             # apply
python manage.py reap_stale_campaigns --loop      # sidecar process
```

## Troubleshooting

```bash
# Do the WhatsApp selectors still match the live page?
python manage.py whatsapp_preflight --number +2348161234765
```

The automation matches CSS and text selectors against a third-party UI. When
WhatsApp changes their login page, the symptom is otherwise a campaign that
sits for ten minutes and fails with every contact marked failed — telling you
nothing about why. The preflight checks each selector against the live page in
about 30 seconds and names the one that broke. It uses a throwaway profile and
never touches a linked session.

> **Last verified against the live page: 2026-10-05 — all 8 checks passed.**
> Every automated test uses a fake browser, so nothing in CI can catch a
> selector WhatsApp has changed; a broken selector passes all 542 tests and
> fails every real send. Re-run the preflight before trusting a send, and
> treat any date more than a few weeks old as unknown.

Set `WHATSAPP_DEBUG=true` to have it also save screenshots of each step.

Errors are always `{"error": "...", "code": "..."}` so the client can show the
real message and branch on the code.

**Campaign creation rejects rather than silently dropping contacts.** If any
number is unusable it returns `400` with `rejected_count` and examples. The
original behaviour created the campaign from whatever validated and said
nothing about the rest, so you watched a progress bar for a smaller list with
no idea numbers had disappeared.

**Campaign media is never served from `MEDIA_ROOT` directly.** It streams
through an ownership-checked endpoint. Serving `MEDIA_ROOT` with `static()` —
as this used to do — exposed every uploaded photo to anyone who could guess
the filename.

---

## Contributing

Before changing anything, read **[AGENTS.md](AGENTS.md)**. It records the traps
in this repo that have actually cost time — the leaked `DEBUG` variable that
breaks both suites, the ambiguity that masqueraded as a flaky frontend test,
and the standard for proving a fix works (revert it and watch the test go red).

## Tests

```bash
# Backend — 367 tests
cd backend
python manage.py test sender --settings=config.settings_test

# Frontend — 175 tests
cd frontend
npm test
npm run lint
```

### The tests ignore your shell

Both suites pin the environment variables they depend on rather than
inheriting whatever your shell exports, because `DEBUG` in particular is read
by both tools for unrelated purposes:

- **Django.** `settings.env_bool` rejects unrecognised booleans on purpose
  (it's what catches `DEBUG=flase`), so a stray `DEBUG='-LH:*, LH:*:error'`
  left over from captured test output made every `manage.py test` invocation
  die at import. `config/settings_test.py` pins `DEBUG`, `SECRET_KEY`,
  `CRYPTOGRAPHY_KEYS` and `WHATSAPP_DISABLE_AUTOMATION` before importing
  settings, so a leaked `DEBUG=false` can't trip the production guards either.
- **Vitest.** Vite reads `process.env.DEBUG` as a `debug` namespace filter,
  and it does so while *building its config* — before any test runs and
  before `test.env` in a vitest config would apply. The same leaked value
  collapsed the run to worker-startup timeouts — 42 of 173 tests executing,
  94s instead of 25s. `vite.config.js` therefore deletes `process.env.DEBUG`
  at the top of the file, before `defineConfig()` is evaluated. (Setting it
  via `test.env` does *not* work for this, and additionally destabilises
  worker startup.)

Both are pinned unconditionally, so a test run is a function of the code and
not of the machine. Tests that genuinely need `DEBUG=False` or the production
guards assert that explicitly against a controlled environment rather than
relying on ambient state.

The same hazard applies to `manage.py` commands that load the default settings
module rather than the test one — `makemigrations --check` dies on a stray
`DEBUG` for the same reason. `config/settings_test.py` is the settings module
to use for anything you want to be runnable anywhere.

### Smoke test

Unit tests use Django's test client, so they don't exercise the real HTTP
stack, middleware or SQLite. This does:

```bash
# terminal 1
set WHATSAPP_DISABLE_AUTOMATION=true    # Windows; export on macOS/Linux
python manage.py runserver 127.0.0.1:8777 --noreload

# terminal 2
python smoke.py
```

28 checks over the whole API: registration, phone validation, campaign
creation (including that a rejected campaign persists nothing), progress
counts, cross-user isolation on every endpoint, and the send guards.

`WHATSAPP_DISABLE_AUTOMATION=true` is required. Without it the send path opens
a real browser at web.whatsapp.com, which is slow, needs a display, and looks
like abuse to WhatsApp. With it set, contacts are marked sent without any
network call — so you can exercise the send flow safely. It logs a warning
every time it's used.

**Do not set that variable for the unit suite.** It short-circuits
`send_whatsapp_messages` before the browser code runs, so the automation tests
silently start asserting against a stub that marks everything sent — about a
dozen failures that look like pre-existing breakage but are your own shell.
`config/settings_test.py` now forces it off, so a leaked value can't do this.

Note the asymmetry, which is deliberate: the unit suite is hermetic and runs
anywhere, while `smoke.py` loads the **real** settings module on purpose. A
genuinely invalid `DEBUG` should fail loudly there — that is the strict parsing
doing its job, not a bug to paper over.

The backend uses a separate settings module because Django's default PBKDF2
hasher costs ~0.5s per hash and this suite creates users constantly.

Browser automation is tested against a fake Playwright rather than a real
browser. The actual UI interaction can't be asserted in CI, but everything
around it can: session reuse, the retry-after-wipe path, pacing, phone-entry
fallbacks, status settling, and the rules that stop a retry re-sending to
people who already received the message.

Four tests skip by design: the quota concurrency tests, because Django's
in-memory test database serialises writes with a table lock and cannot
exercise concurrent reservations. They run against a file-backed or Postgres
database.

### Continuous integration

`.github/workflows/ci.yml` runs the backend suite, `check --deploy`, a
pending-migration check, and the frontend lint/test/build. Every step in it
was run locally before being committed.

Two details worth knowing:

- The deploy check generates a throwaway Fernet key rather than using a
  literal. `encryption.py` builds a cipher from each configured key at import,
  so a placeholder fails every test that touches an encrypted field.
- The smoke test is **not** on the push path. It is manual-dispatch-only,
  because it starts a real browser against web.whatsapp.com unless automation
  is disabled.

`.pre-commit-config.yaml` mirrors those checks locally. Black is installed at
`manual` stage only: the codebase has no committed Black style, and
reformatting everything would bury substantive changes. Remove that hook once
formatting has been done deliberately.

---

## Layout

```
backend/
  config/settings.py         env loading, production guards, cache, throttling
  sender/
    models.py                Campaign, Contact, WhatsAppLoginState, quota
    encryption.py            Fernet fields with key rotation
    phone_numbers.py         validation, normalization, country resolution
    views.py                 auth, profile, create, status, media, start
    campaigns.py             list, retry, delete
    reliability.py           stale-send detection and recovery
    whatsapp_automation.py   Playwright driver
    throttles.py             auth and send-start limits
    management/commands/
      whatsapp_preflight.py  verify selectors against the live page
      reap_stale_campaigns.py recover from dead workers
    tests/
  Dockerfile + docker/         Xvfb-wrapped image for the browser automation
  smoke.py                     live end-to-end checks against a running server
frontend/
  src/api.js                   single fetch layer with real error handling
  src/utils/phone.js           client-side validation via libphonenumber-js
  src/components/              UI, including CampaignList for history
.github/workflows/ci.yml       backend + frontend checks
.pre-commit-config.yaml        the same checks, run before a push
docker-compose.yml             local stack; sessions on a persistent volume
```

---

## Known limitations

- **Requires a desktop session.** See "Headless vs headed" above.
- **Fragile against WhatsApp changes.** The selectors are matched against a
  third-party UI. Run `manage.py whatsapp_preflight` when a send breaks
  unexpectedly — it names the broken selector instead of leaving you with a
  ten-minute timeout and a failed list.
- **No delivery receipts.** "sent" means the send button was clicked, not that
  the message was delivered or read.
- **SQLite only.** `DATABASE_PATH` is configurable, but the concurrency here
  assumes a single writer. Sends write from background threads while requests
  are served, so this works for a handful of users and gets uncomfortable
  before long. Postgres is the right move before that, and `docker-compose.yml`
  has an opt-in profile for it — but it is not wired into `settings.py` yet,
  deliberately: switching database backends should be a decision, not a
  default.
- **No scheduled campaigns.** Sends start when you press the button.
- **Retry is per-campaign, not selective.** You can't retry just the failures
  from a specific day or subset — every `failed` contact comes back.
- **Rate limits are per-process unless you set `REDIS_URL`.** The file-cache
  default is shared between workers on one box, but not across hosts.
- **Login codes are stored in plaintext** in `WhatsAppLoginState`. They're
  only useful for the few minutes before the device is linked, and encrypting
  them would buy nothing against an attacker who can already read the row.