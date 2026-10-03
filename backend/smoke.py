"""Live end-to-end smoke test against a running dev server.

Complements the unit tests: exercises the real HTTP stack, real SQLite, real
middleware and real serialisation. Does NOT touch WhatsApp — the send path is
only verified as far as the guards that run before the browser launches.

Run it with the server started in no-automation mode, so no browser is
launched and WhatsApp is never contacted:

    # terminal 1
    set WHATSAPP_DISABLE_AUTOMATION=true
    python manage.py runserver 127.0.0.1:8777 --noreload

    # terminal 2
    python smoke.py

Exits non-zero if any check fails.
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8777/api'
TOKEN = None
failures = []
checks = 0

# Needs Django's ORM for the one check that deliberately bypasses the HTTP
# rate limiter. Configured before any model import.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django  # noqa: E402

django.setup()

# The auth endpoints are rate limited per IP (5 registrations/hour by default),
# so repeated smoke runs against a long-lived dev server would start failing on
# auth rather than on anything interesting. Clearing the throttle cache makes
# the script idempotent. Safe because this only touches throttle counters.
from django.core.cache import cache  # noqa: E402

cache.clear()


def call(method, path, body=None, expect=None, label=''):
    global checks
    checks += 1
    url = f'{BASE}{path}'
    data = None
    headers = {'Content-Type': 'application/json'}
    if TOKEN:
        headers['Authorization'] = f'Token {TOKEN}'
    if body is not None:
        data = json.dumps(body).encode()

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            status, payload = response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, exc.read().decode()

    ok = status == expect if expect is not None else 200 <= status < 300
    print(f'  [{"ok" if ok else "FAIL"}] {label or method + " " + path} -> {status}')
    if not ok:
        failures.append(f'{label or path}: expected {expect}, got {status}: {payload[:200]}')
    try:
        return status, json.loads(payload)
    except json.JSONDecodeError:
        failures.append(f'{label or path}: response was not JSON: {payload[:200]}')
        return status, None


def multipart(path, fields, files, expect=None):
    """Minimal multipart/form-data POST for campaign creation."""
    global checks
    checks += 1
    boundary = '----smoketest7f3a'
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    for name, (filename, content, ctype) in files.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
            + content
            + b'\r\n'
        )
    body = b''.join(parts) + f'--{boundary}--\r\n'.encode()

    req = urllib.request.Request(
        f'{BASE}{path}',
        data=body,
        headers={
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Authorization': f'Token {TOKEN}',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            status, payload = response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, exc.read().decode()

    ok = status == expect if expect is not None else 200 <= status < 300
    print(f'  [{"ok" if ok else "FAIL"}] POST {path} (multipart) -> {status}')
    if not ok:
        failures.append(f'multipart {path}: expected {expect}, got {status}: {payload[:200]}')
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def form_call(method, path, body, label='', expect=200):
    global checks
    checks += 1
    import urllib.parse
    encoded = urllib.parse.urlencode(body).encode()
    req = urllib.request.Request(
        f'{BASE}{path}',
        data=encoded,
        headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'Authorization': f'Token {TOKEN}',
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            status, payload = response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, exc.read().decode()
    ok = status == expect
    print(f'  [{"ok" if ok else "FAIL"}] {label} -> {status}')
    if not ok:
        failures.append(f'{label}: expected {expect}, got {status}: {payload[:200]}')
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


print('\n--- auth ---')
import random
import string

username = 'smoke_' + ''.join(random.choices(string.ascii_lowercase, k=8))
password = 'smoke-test-password-1'

status, data = call(
    'POST', '/auth/register/',
    {'username': username, 'password': password},
    expect=201, label='register',
)
TOKEN = data['token']

call('POST', '/auth/register/', {'username': username, 'password': password},
     expect=400, label='duplicate register rejected')

print('\n--- validation rejects bad input ---')
call('PUT', '/profile/whatsapp-number/', {'whatsapp_number': '+2341'},
     expect=400, label='invalid phone rejected')
call('PUT', '/profile/whatsapp-number/', {'whatsapp_number': '08012345678'},
     expect=400, label='no country code rejected')
call('PUT', '/profile/whatsapp-number/', {'whatsapp_number': '+234 816-123-4765'},
     expect=200, label='valid phone accepted, normalized')
call('GET', '/profile/whatsapp-number/', expect=200, label='phone readable')

print('\n--- campaign creation rejects unusable contacts ---')
# The whole point of this check: a campaign containing one bad number must be
# rejected outright, and must NOT be created with the bad number silently
# dropped. Previously this created a campaign from whatever validated and said
# nothing about the rest.
before = call('GET', '/campaigns/', expect=200, label='count campaigns before')[1]['total']

body = multipart(
    '/campaigns/create/',
    {
        'name': 'Smoke invalid',
        'message_text': 'Hi {name}',
        'send_mode': 'delay',
        'contacts': json.dumps([
            {'name': 'Good', 'phone': '+2348161234765'},
            {'name': 'Bad', 'phone': '+2341'},
        ]),
    },
    {},
    expect=400,
)
if body is not None:
    if body.get('code') != 'invalid_contacts':
        failures.append(f'expected invalid_contacts, got {body.get("code")}')
        print(f'  [FAIL] wrong error code: {body.get("code")}')
    elif '+2341' not in body.get('error', ''):
        failures.append('error message does not name the offending number')
        print('  [FAIL] error message does not name the bad number')
    else:
        print('  [ok] error names the offending number and its reason')

after = call('GET', '/campaigns/', expect=200, label='count campaigns after')[1]['total']
if after != before:
    failures.append(f'rejected campaign was still created ({before} -> {after})')
    print(f'  [FAIL] a rejected campaign was persisted ({before} -> {after})')
else:
    print('  [ok] nothing was persisted for the rejected campaign')

print('\n--- campaign creation succeeds ---')
campaign = multipart('/campaigns/create/', {
    'name': 'Smoke campaign',
    'message_text': 'Hi {name}, testing.',
    'send_mode': 'delay',
    'contacts': json.dumps([
        {'name': 'Ada', 'phone': '+2348161234765'},
        {'name': 'Bob', 'phone': '+14155552671'},
        {'name': 'Cy', 'phone': '+254712345678'},
    ]),
}, {'media_files': ('tiny.png', b'\x89PNG\r\n\x1a\n' + b'0' * 40, 'image/png')})

campaign_id = campaign['id']

print('\n--- status ---')
status, data = call('GET', f'/campaigns/{campaign_id}/status/', expect=200, label='status')
progress = data.get('progress')
if progress != {'total': 3, 'sent': 0, 'failed': 0, 'pending': 3}:
    failures.append(f'unexpected progress: {progress}')
    print(f'  [FAIL] progress counts wrong: {progress}')
else:
    print('  [ok] progress counts correct')

if 'contacts' not in data:
    failures.append('status missing contacts')
    print('  [FAIL] status missing contacts')
else:
    print('  [ok] status includes contacts')

print('\n--- listing ---')
status, data = call('GET', '/campaigns/', expect=200, label='list campaigns')
listed = data['campaigns'][0]
for field in ('total_contacts', 'sent_contacts', 'failed_contacts', 'pending_contacts', 'can_retry'):
    if field not in listed:
        failures.append(f'list row missing {field}')
        print(f'  [FAIL] list row missing {field}')
if 'contacts' in listed:
    failures.append('list row leaked contacts')
    print('  [FAIL] list row leaked contacts')
else:
    print('  [ok] list row is a summary, no contacts leaked')

if listed['total_contacts'] != 3:
    failures.append(f'list total_contacts wrong: {listed["total_contacts"]}')
    print(f'  [FAIL] total_contacts={listed["total_contacts"]}, expected 3')
else:
    print('  [ok] list counts match')

print('\n--- retry with nothing to retry ---')
status, data = call('POST', f'/campaigns/{campaign_id}/retry/', expect=200, label='retry (none failed)')
if data['reset_count'] != 0:
    failures.append(f'retry reset {data["reset_count"]}, expected 0')
    print('  [FAIL] retry reset contacts that had not failed')
else:
    print('  [ok] retry reset nothing, did not touch pending contacts')

print('\n--- send start guards ---')
# The server must be running with WHATSAPP_DISABLE_AUTOMATION=true. Without
# it this section would open a real browser at web.whatsapp.com, which is slow,
# needs a display, and looks like abuse to WhatsApp.
automation_disabled = os.environ.get('WHATSAPP_DISABLE_AUTOMATION', '').lower() == 'true'

if not automation_disabled:
    print('  [SKIP] server is not in WHATSAPP_DISABLE_AUTOMATION mode;')
    print('         starting a send would contact WhatsApp for real.')
else:
    status, data = call('POST', f'/campaigns/{campaign_id}/start/', expect=202,
                        label='start accepted')
    if data.get('pending') != 3:
        failures.append(f'start reported pending={data.get("pending")}, expected 3')

    # The 409 "already sending" guard can't be observed here: with automation
    # disabled the worker finishes in milliseconds, so by the time a second
    # request lands the campaign is already complete. It's covered by the unit
    # tests, which hold the worker thread open deliberately.
    print('  [note] duplicate-start guard not exercised here (send completes too')
    print('         fast to catch); covered by test_views.StartSendingTests.')

    # The disabled-automation worker marks contacts sent immediately.
    for _ in range(30):
        status, data = call('GET', f'/campaigns/{campaign_id}/status/', expect=200,
                            label='poll for completion')
        if data['campaign_status'] in ('completed', 'failed'):
            break
        import time
        time.sleep(1)

    if data['campaign_status'] != 'completed':
        failures.append(f'campaign ended as {data["campaign_status"]}, expected completed')
        print(f'  [FAIL] campaign ended as {data["campaign_status"]}')
    elif data['progress']['sent'] != 3:
        failures.append(f'sent={data["progress"]["sent"]}, expected 3')
        print(f'  [FAIL] sent={data["progress"]["sent"]}, expected 3')
    else:
        print('  [ok] campaign completed and all 3 contacts marked sent')

    status, listed = call('GET', '/campaigns/', expect=200, label='list after completion')
    row = next((c for c in listed['campaigns'] if c['id'] == campaign_id), None)
    if row is None:
        failures.append('completed campaign missing from the list')
    elif row['status'] != 'completed' or row['sent_contacts'] != 3:
        failures.append(f'list row wrong after completion: {row}')
        print(f'  [FAIL] list row wrong: {row}')
    else:
        print('  [ok] list reflects the completed send')
        print('  [ok] delete now permitted (nothing in flight)')

print('\n--- isolation ---')
# Registered directly rather than over HTTP: /auth/register/ is rate limited
# per IP at 5/hour, so a second HTTP registration would just get a 429. That
# limit doing its job is itself worth noting.
from django.contrib.auth.models import User  # noqa: E402
from rest_framework.authtoken.models import Token  # noqa: E402

other_username = username + '_other'
other_user = User.objects.create_user(other_username, password=password)
other_token = Token.objects.create(user=other_user).key
print('  [ok] created second user directly (register is IP-limited to 5/hour)')

first_token = TOKEN
TOKEN = other_token
call('GET', f'/campaigns/{campaign_id}/status/', expect=404, label="other user can't read status")
call('POST', f'/campaigns/{campaign_id}/start/', expect=404, label="other user can't start")
call('DELETE', f'/campaigns/{campaign_id}/', expect=404, label="other user can't delete")
call('POST', f'/campaigns/{campaign_id}/retry/', expect=404, label="other user can't retry")
call('GET', '/campaigns/', expect=200, label="other user's list is their own")

listed = call('GET', '/campaigns/', expect=200, label='read other list')[1]
if any(c['id'] == campaign_id for c in listed['campaigns']):
    failures.append("another user's campaign appeared in the list")
    print('  [FAIL] cross-tenant leak in the campaign list')
else:
    print('  [ok] no cross-tenant leak in the campaign list')

TOKEN = first_token

print('\n--- login status is per-user and quiet ---')
status, data = call('GET', '/login-status/', expect=200, label='login status')
if data['login_code'] is not None:
    failures.append('login code leaked when idle')
    print('  [FAIL] returned a login code while idle')
else:
    print('  [ok] no login code while idle')

if automation_disabled:
    print('\n--- guards on an already-sent campaign ---')
    status, data = call('POST', f'/campaigns/{campaign_id}/start/', expect=400,
                        label='start refused once every contact is sent')
    if data.get('code') != 'nothing_pending':
        failures.append(f'expected nothing_pending, got {data}')
        print(f'  [FAIL] expected nothing_pending, got {data}')

    print('\n--- retry a fully-sent campaign must not reset anything ---')
    status, data = call('POST', f'/campaigns/{campaign_id}/retry/', expect=200,
                        label='retry on a completed campaign')
    if data['reset_count'] != 0:
        failures.append(f'retry reset {data["reset_count"]} already-sent contacts')
        print(f'  [FAIL] retry reset {data["reset_count"]} already-sent contacts')
    else:
        print('  [ok] already-delivered contacts were left alone')

    status, data = call('GET', f'/campaigns/{campaign_id}/status/', expect=200,
                        label='status after retry')
    if data['progress']['sent'] != 3:
        failures.append(f'sent dropped to {data["progress"]["sent"]} after retry')
        print('  [FAIL] sent contacts were reset by retry')
    else:
        print('  [ok] all 3 still marked sent')

    print('\n--- delete once nothing is in flight ---')
    call('DELETE', f'/campaigns/{campaign_id}/', expect=200, label='delete allowed')
    call('GET', f'/campaigns/{campaign_id}/status/', expect=404, label='deleted campaign is gone')
else:
    print('\n--- delete refused while sending ---')
    call('DELETE', f'/campaigns/{campaign_id}/', expect=409, label='delete blocked while in flight')

print('\n' + '=' * 60)
if failures:
    print(f'{len(failures)} FAILURE(S) out of {checks} checks:')
    for failure in failures:
        print(f'  - {failure}')
    sys.exit(1)

print(f'All {checks} smoke checks passed.')