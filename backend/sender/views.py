"""
API views.

Changes worth calling out relative to the original:

* Campaign creation no longer silently discards contacts whose phone number
  doesn't validate. It returns a 400 explaining exactly how many were
  rejected and showing examples, so nobody discovers at send time that a
  third of their list evaporated.

* /login-status/ reads from the database instead of flag files in MEDIA_ROOT,
  which don't work across gunicorn workers or survive a redeploy.

* Starting a send is guarded: one send per campaign, one send per user, and
  a daily contact quota. Previously you could POST /start/ repeatedly and
  spawn multiple Chromium processes against the same browser profile.

* Campaign media is served through an ownership-checked endpoint instead of
  Django's static() helper, which exposed every uploaded file to anyone who
  could guess the URL.
"""

import json
import logging
import mimetypes
import os
import threading

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core.exceptions import SuspiciousFileOperation
from django.db import IntegrityError, transaction
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    parser_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import Campaign, Contact, DailySendQuota, MediaFile, UserProfile, WhatsAppLoginState
from .phone_numbers import PhoneValidationError, to_e164
from .reliability import reap_stale_campaigns
from .serializers import CampaignSerializer, UserProfileSerializer
from .throttles import (
    CampaignCreateThrottle,
    LoginRateThrottle,
    LoginStatusPollThrottle,
    RegisterRateThrottle,
    SendStartThrottle,
    StatusPollThrottle,
)
from .whatsapp_automation import send_whatsapp_messages

logger = logging.getLogger(__name__)

MAX_CONTACTS_PER_CAMPAIGN = 5000

# How many rejected numbers to quote back in the error. Enough for the user
# to spot the pattern (wrong column mapped? all leading zeros?) without
# turning the response into a copy of their CSV.
MAX_REJECTED_EXAMPLES = 5

ALLOWED_MEDIA_PREFIXES = ('image/', 'video/')

# Guards against two requests racing to start sends for the same user before
# either has written its status. A process-local lock, which is exactly
# sufficient for the dev server and for gunicorn with one worker; the
# database status check is what actually enforces correctness across
# workers, this just avoids the wasteful duplicate work.
_send_lock = threading.Lock()
_active_sends = set()


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


# --- Auth ----------------------------------------------------------------


@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])
@throttle_classes([RegisterRateThrottle])
def register_user(request):
    username = (request.data.get('username') or '').strip()
    password = request.data.get('password') or ''
    email = (request.data.get('email') or '').strip()

    if not username or not password:
        return Response(
            {'error': 'Username and password are required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if len(username) > 150:
        return Response(
            {'error': 'That username is too long.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        with transaction.atomic():
            user = User.objects.create_user(username=username, password=password, email=email)
            # Created up front rather than lazily on first profile read, so a
            # new account is never momentarily missing its own profile row.
            UserProfile.objects.create(user=user)
            token, _ = Token.objects.get_or_create(user=user)
    except IntegrityError:
        # Concurrent registration of the same username, rather than a
        # pre-check that can be raced.
        return Response(
            {'error': 'That username is already taken.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return Response(
        {'token': token.key, 'username': user.username},
        status=status.HTTP_201_CREATED,
    )


@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])
@throttle_classes([LoginRateThrottle])
def login_user(request):
    username = (request.data.get('username') or '').strip()
    password = request.data.get('password') or ''

    if not username or not password:
        return Response(
            {'error': 'Username and password are required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = authenticate(username=username, password=password)

    if user is None:
        # Deliberately identical message for "no such user", "wrong password"
        # and "account disabled" so this can't be used to enumerate valid
        # usernames or discover which accounts are active. Authentication
        # failure is logged so the operator still has a record of a
        # password-guessing attempt.
        logger.warning(
            'Failed login attempt for username=%r from %s', username, _client_ip(request)
        )
        return Response(
            {'error': 'Invalid username or password.'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    token, _ = Token.objects.get_or_create(user=user)
    return Response({'token': token.key, 'username': user.username})


# --- Profile -------------------------------------------------------------


@api_view(['GET', 'PUT'])
def whatsapp_profile(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == 'GET':
        return Response(UserProfileSerializer(profile).data)

    raw_number = str(request.data.get('whatsapp_number', '')).strip()

    if not raw_number:
        profile.whatsapp_number = ''
        profile.save(update_fields=['whatsapp_number'])
        return Response(UserProfileSerializer(profile).data)

    try:
        cleaned = to_e164(raw_number)
    except PhoneValidationError as exc:
        return Response(
            {
                'error': f'"{raw_number}" {exc.reason}. Include your country code, '
                'e.g. +2348161234765.',
                'code': 'invalid_phone',
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    profile.whatsapp_number = cleaned
    profile.save(update_fields=['whatsapp_number'])

    # The number is part of the login handshake, so changing it invalidates
    # whatever state the browser session was mid-way through.
    WhatsAppLoginState.objects.filter(user=request.user).delete()

    return Response(UserProfileSerializer(profile).data)


# --- Campaigns -----------------------------------------------------------


def _validate_contacts(raw_contacts):
    """
    Normalizes and validates each contact.

    Returns (valid, rejected). `rejected` is a list of
    {'name', 'phone', 'reason'} for everything that couldn't be used.
    """
    valid = []
    rejected = []

    for index, contact in enumerate(raw_contacts):
        if not isinstance(contact, dict):
            rejected.append(
                {'name': '', 'phone': str(contact)[:40], 'reason': 'entry is not a valid contact'}
            )
            continue

        name = str(contact.get('name', '') or '').strip()[:255]
        raw_phone = contact.get('phone', '')

        try:
            phone = to_e164(raw_phone)
        except PhoneValidationError as exc:
            rejected.append({
                'name': name,
                'phone': str(raw_phone)[:40],
                'reason': exc.reason,
            })
            continue

        valid.append({'name': name, 'phone': phone})

    return valid, rejected


@api_view(['POST'])
@throttle_classes([CampaignCreateThrottle])
@parser_classes([MultiPartParser, FormParser])
def create_campaign(request):
    name = str(request.data.get('name') or 'Untitled Campaign').strip()[:255] or 'Untitled Campaign'
    message_text = str(request.data.get('message_text') or '').strip()
    send_mode = str(request.data.get('send_mode') or Campaign.SendMode.DELAY)

    if send_mode not in Campaign.SendMode.values:
        return Response(
            {'error': f'Unknown send mode "{send_mode}".', 'code': 'invalid_send_mode'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not message_text:
        return Response(
            {'error': 'A message is required.', 'code': 'empty_message'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if len(message_text) > 4096:
        return Response(
            {'error': 'That message is too long (max 4096 characters).'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    contacts_raw = request.data.get('contacts', '[]')
    try:
        contacts_list = json.loads(contacts_raw) if isinstance(contacts_raw, str) else contacts_raw
    except json.JSONDecodeError:
        return Response(
            {'error': 'Contacts could not be parsed as JSON.', 'code': 'bad_contacts'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not isinstance(contacts_list, list):
        return Response(
            {'error': 'Contacts must be a list.', 'code': 'bad_contacts'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not contacts_list:
        return Response(
            {'error': 'At least one contact is required.', 'code': 'no_contacts'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if len(contacts_list) > MAX_CONTACTS_PER_CAMPAIGN:
        return Response(
            {'error': f'Too many contacts — max {MAX_CONTACTS_PER_CAMPAIGN} per campaign.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    valid_contacts, rejected = _validate_contacts(contacts_list)

    # The original code created the campaign from whatever survived
    # validation and said nothing about the rest. The UI showed "campaign
    # created", a progress bar ticked up to a smaller total, and the user
    # never learned which numbers were skipped. Refusing is the only way to
    # make the loss visible.
    if rejected:
        examples = rejected[:MAX_REJECTED_EXAMPLES]
        detail_lines = [
            f'"{item["phone"] or "(blank)"}" {item["reason"]}'
            for item in examples
        ]
        if len(rejected) > MAX_REJECTED_EXAMPLES:
            detail_lines.append(f'…and {len(rejected) - MAX_REJECTED_EXAMPLES} more')

        return Response(
            {
                'error': (
                    f'{len(rejected)} of {len(contacts_list)} contacts have unusable phone '
                    f'numbers, so nothing was created. Fix or remove them and try again. '
                    f'Examples — {"; ".join(detail_lines)}'
                ),
                'code': 'invalid_contacts',
                'rejected_count': len(rejected),
                'rejected': examples,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    files = request.FILES.getlist('media_files')
    media_error = _validate_media(files)
    if media_error:
        return Response(media_error, status=status.HTTP_400_BAD_REQUEST)

    campaign = Campaign.objects.create(
        owner=request.user,
        name=name,
        message_text=message_text,
        send_mode=send_mode,
        status=Campaign.Status.DRAFT,
    )

    Contact.objects.bulk_create([
        Contact(campaign=campaign, name=c['name'], phone=c['phone'])
        for c in valid_contacts
    ])

    for file_obj in files:
        MediaFile.objects.create(campaign=campaign, file=file_obj)

    return Response(CampaignSerializer(campaign).data, status=status.HTTP_201_CREATED)


def _validate_media(files):
    """
    Rejects uploads that are too large or not an accepted media type.

    Returns None if everything is fine, or an (error_body, ) tuple to
    return. Size is enforced here because DATA_UPLOAD_MAX_MEMORY_SIZE only
    bounds the whole request, not any single file within it.
    """
    if not files:
        return None

    max_bytes = getattr(settings, 'MAX_MEDIA_BYTES', 25 * 1024 * 1024)

    for file_obj in files:
        if file_obj.size > max_bytes:
            return {
                'error': (
                    f'"{file_obj.name}" is larger than the '
                    f'{max_bytes // (1024 * 1024)}MB limit.'
                ),
                'code': 'media_too_large',
            }

        content_type = (file_obj.content_type or '').lower()
        if not content_type.startswith(ALLOWED_MEDIA_PREFIXES):
            return {
                'error': f'"{file_obj.name}" is not a supported attachment (images and videos only).',
                'code': 'media_bad_type',
            }

    return None


@api_view(['GET'])
@throttle_classes([StatusPollThrottle])
def get_campaign_status(request, campaign_id):
    try:
        campaign = Campaign.objects.get(id=campaign_id, owner=request.user)
    except Campaign.DoesNotExist:
        return Response(
            {'error': 'Campaign not found.', 'code': 'not_found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    data = CampaignSerializer(campaign).data
    progress = campaign.progress()
    data['progress'] = progress
    data['campaign_status'] = campaign.status
    data['status_detail'] = campaign.status_detail
    return Response(data)


# --- Media ---------------------------------------------------------------


@api_view(['GET'])
def serve_media_file(request, campaign_id, file_id):
    """
    Streams a campaign attachment to its owner only.

    Replaces MEDIA_URL/static(), which served the whole of MEDIA_ROOT to
    anyone on the internet who could guess a filename.
    """
    try:
        media = MediaFile.objects.select_related('campaign').get(
            id=file_id, campaign_id=campaign_id, campaign__owner=request.user
        )
    except MediaFile.DoesNotExist:
        return Response(
            {'error': 'File not found.', 'code': 'not_found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    path = media.file.path
    if not os.path.exists(path):
        # The row survives but the file is gone — which happens on any
        # PaaS deploy with an ephemeral MEDIA_ROOT. Worth a distinct log
        # line because it's a real data-loss signal, not a client error.
        logger.error('MediaFile %s is missing from disk: %s', media.pk, path)
        return Response(
            {'error': 'That file is no longer available.', 'code': 'file_missing'},
            status=status.HTTP_404_NOT_FOUND,
        )

    content_type = mimetypes.guess_type(path)[0] or 'application/octet-stream'
    return FileResponse(
        media.file.open('rb'),
        content_type=content_type,
        # Attachments are user-supplied; never let the browser sniff one
        # as HTML and execute it in our origin.
        as_attachment=True,
        filename=os.path.basename(path),
    )


# --- Login status --------------------------------------------------------


@api_view(['GET'])
@throttle_classes([LoginStatusPollThrottle])
def get_login_status(request):
    """
    The current state of this user's WhatsApp handshake.

    Read from the database rather than flag files: files don't survive a
    redeploy and don't work when the HTTP worker and the automation thread
    are in different gunicorn workers.
    """
    state, _ = WhatsAppLoginState.objects.get_or_create(
        user=request.user, defaults={'phase': WhatsAppLoginState.Phase.IDLE}
    )

    code_ready = bool(state.login_code) and state.phase in {
        WhatsAppLoginState.Phase.WAITING_FOR_CODE,
        WhatsAppLoginState.Phase.AWAITING_LOGIN,
        WhatsAppLoginState.Phase.SLOW,
    }

    return Response({
        'phase': state.phase,
        'code_ready': code_ready,
        'login_code': state.login_code if code_ready else None,
        'login_slow': state.phase == WhatsAppLoginState.Phase.SLOW,
        'login_failed': state.phase == WhatsAppLoginState.Phase.FAILED,
        'login_code_error': state.error or None,
        'updated_at': state.updated_at,
    })


# --- Sending -------------------------------------------------------------


def _set_login_state(user_id, **fields):
    """Updates the stored handshake state for a user, ignoring races."""
    try:
        state, _ = WhatsAppLoginState.objects.get_or_create(
            user_id=user_id, defaults={'phase': WhatsAppLoginState.Phase.IDLE}
        )
        for key, value in fields.items():
            setattr(state, key, value)
        state.save()
    except Exception:
        # A failure to record progress must not kill an in-flight send.
        logger.exception('Could not persist WhatsApp login state for user %s', user_id)


def run_campaign_send(campaign_id, user_id):
    """
    The worker body. Runs on a background thread.

    Playwright's sync API cannot run inside an asyncio event loop, which is
    why this is a thread rather than an async task — the old code set
    DJANGO_ALLOW_ASYNC_UNSAFE to silence Django's warning about exactly
    this, which papered over the problem instead of acknowledging it.
    """
    Phase = WhatsAppLoginState.Phase

    try:
        campaign = Campaign.objects.get(id=campaign_id, owner_id=user_id)
    except Campaign.DoesNotExist:
        logger.warning('Campaign %s vanished before it could start', campaign_id)
        return

    # A reaper may have marked this failed between queueing and the thread
    # actually being scheduled (worker recycling, slow thread start). Starting
    # anyway would stomp on the retry the user has already kicked off.
    if campaign.status == Campaign.Status.FAILED:
        logger.info('Campaign %s was marked failed before its worker started; skipping', campaign_id)
        return

    profile = UserProfile.objects.filter(user_id=user_id).first()
    if not profile or not profile.whatsapp_number:
        _finish_campaign(campaign, Campaign.Status.FAILED, 'No WhatsApp number is set.')
        return

    # Fresh handshake state for this run. Anything left over from a previous
    # campaign would otherwise make the UI show a stale code.
    WhatsAppLoginState.objects.filter(user_id=user_id).delete()

    contacts = list(campaign.contacts.filter(status='pending').values('id', 'name', 'phone'))
    if not contacts:
        _finish_campaign(campaign, Campaign.Status.COMPLETED, 'No pending contacts.')
        return

    now = timezone.now()
    campaign.status = Campaign.Status.RUNNING
    campaign.status_detail = ''
    campaign.send_started_at = now
    campaign.last_heartbeat_at = now
    campaign.save(update_fields=[
        'status', 'status_detail', 'send_started_at', 'last_heartbeat_at',
    ])

    media_paths = []
    for media in campaign.media_files.all():
        # Narrowed from `except Exception`. Reading FileFieldFile.path has a
        # knowable failure set (django/db/models/fields/files.py and
        # core/files/storage/base.py): ValueError when the row has no file
        # associated, NotImplementedError from a storage backend that has no
        # local path, and SuspiciousFileOperation from safe_join on a name that
        # escapes the media root. A bare handler here would also swallow a
        # genuine AttributeError or typo and log it as a missing file, which is
        # the same masking that commit e904a92 removed elsewhere.
        try:
            path = media.file.path
        except (ValueError, NotImplementedError, SuspiciousFileOperation):
            logger.exception('Could not resolve media path for %s', media.pk)
            continue

        if os.path.exists(path):
            media_paths.append(path)
        else:
            logger.error('MediaFile %s missing on disk at send time', media.pk)

    delay_seconds = 3 if campaign.send_mode == Campaign.SendMode.DELAY else 1

    def update_status(contact_id, contact_status):
        """
        Settles one contact and refreshes the campaign heartbeat.

        The heartbeat is what distinguishes a live send from one whose worker
        died. Written in the same statement batch as the contact update so
        there is no window where a campaign looks idle mid-send.
        """
        Contact.objects.filter(id=contact_id).update(status=contact_status)
        Campaign.objects.filter(id=campaign_id).update(last_heartbeat_at=timezone.now())

    try:
        result = send_whatsapp_messages(
            contacts=contacts,
            message_text=campaign.message_text,
            user_id=user_id,
            whatsapp_number=profile.whatsapp_number,
            media_paths=media_paths,
            delay_seconds=delay_seconds,
            on_progress=update_status,
            on_login_slow=lambda: _set_login_state(user_id, phase=Phase.SLOW),
            on_login_failed=lambda: _set_login_state(
                user_id, phase=Phase.FAILED, login_code='', error='WhatsApp login did not complete.'
            ),
            on_login_code=lambda code: _set_login_state(
                user_id,
                phase=Phase.WAITING_FOR_CODE,
                login_code=code,
                code_generated_at=timezone.now(),
                error='',
            ),
            on_login_code_error=lambda err: _set_login_state(user_id, error=err),
        )

        if result.get('logged_in'):
            _set_login_state(user_id, phase=Phase.LOGGED_IN, login_code='', error='')
            _finish_campaign(
                campaign,
                Campaign.Status.COMPLETED,
                f'Sent {result["sent"]} message(s), {result["failed"]} failed.',
            )
        else:
            # Defensive: the automation settles contacts on its own failure
            # paths, but if it returned early (a mocked or partially-refactored
            # path) anything still pending would leave the progress bar
            # spinning forever instead of reporting a failure.
            Contact.objects.filter(campaign=campaign, status='pending').update(status='failed')
            _finish_campaign(campaign, Campaign.Status.FAILED, 'WhatsApp login did not complete.')

    except Exception:
        logger.exception('Campaign %s failed unexpectedly', campaign_id)
        Contact.objects.filter(campaign=campaign, status='pending').update(status='failed')
        _finish_campaign(campaign, Campaign.Status.FAILED, 'The send stopped because of an internal error.')

    finally:
        _active_sends.discard((campaign_id, user_id))


def _finish_campaign(campaign, status_value, detail=''):
    now = timezone.now()
    campaign.status = status_value
    campaign.status_detail = detail
    campaign.send_finished_at = now
    campaign.last_heartbeat_at = now
    campaign.save(update_fields=[
        'status', 'status_detail', 'send_finished_at', 'last_heartbeat_at',
    ])


@api_view(['POST'])
@throttle_classes([SendStartThrottle])
def start_sending(request, campaign_id):
    try:
        campaign = Campaign.objects.get(id=campaign_id, owner=request.user)
    except Campaign.DoesNotExist:
        return Response(
            {'error': 'Campaign not found.', 'code': 'not_found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not profile.whatsapp_number:
        return Response(
            {
                'error': 'Add your WhatsApp phone number in settings before sending.',
                'code': 'phone_required',
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    pending = campaign.contacts.filter(status='pending').count()
    if pending == 0:
        return Response(
            {'error': 'This campaign has no contacts left to send to.', 'code': 'nothing_pending'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Lock around the read-modify-write of both the campaign status and the
    # per-user active set, so two rapid clicks can't both pass the check and
    # launch two browsers against one profile directory.
    in_flight = {Campaign.Status.QUEUED, Campaign.Status.RUNNING}

    with _send_lock:
        # Reap abandoned campaigns before checking.
        #
        # The in-memory set only protects one process, and it is empty after a
        # restart or a recycled gunicorn worker. Without the database-level
        # check below, a campaign whose worker died mid-send stayed RUNNING
        # forever and every subsequent start attempt returned 409 with no way
        # for the user to recover except editing the database by hand.
        reap_stale_campaigns()

        # Drop in-memory entries whose campaign is no longer in flight. The
        # worker normally clears its own entry in a finally block, but a hard
        # process kill would otherwise leave a stale key blocking this user
        # from sending forever.
        _active_sends.difference_update({
            (cid, uid) for cid, uid in _active_sends
            if not Campaign.objects.filter(
                id=cid, owner_id=uid, status__in=in_flight
            ).exists()
        })

        campaign.refresh_from_db()

        if campaign.status in in_flight or (campaign_id, request.user.id) in _active_sends:
            return Response(
                {'error': 'This campaign is already sending.', 'code': 'already_sending'},
                status=status.HTTP_409_CONFLICT,
            )

        other_running = Campaign.objects.filter(
            owner=request.user, status__in=in_flight
        ).exclude(id=campaign_id).count()
        if other_running:
            return Response(
                {
                    'error': 'You already have a campaign sending. Wait for it to finish first.',
                    'code': 'user_busy',
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Daily quota. Reserved before the thread starts so the count can't
        # be raced past by concurrent requests.
        quota_limit = _daily_quota_limit()
        reserved = DailySendQuota.reserve(request.user, pending, quota_limit)
        if reserved is None:
            return Response(
                {
                    'error': (
                        f'Daily limit reached — you can send {quota_limit} messages per day. '
                        'Try again tomorrow.'
                    ),
                    'code': 'quota_exceeded',
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        campaign.status = Campaign.Status.QUEUED
        campaign.status_detail = ''
        campaign.send_started_at = None
        campaign.send_finished_at = None
        campaign.last_heartbeat_at = timezone.now()
        campaign.save(update_fields=[
            'status', 'status_detail', 'send_started_at', 'send_finished_at',
            'last_heartbeat_at',
        ])

        _active_sends.add((campaign_id, request.user.id))

    thread = threading.Thread(
        target=run_campaign_send,
        args=(campaign_id, request.user.id),
        name=f'campaign-send-{campaign_id}',
        daemon=True,
    )
    thread.start()

    return Response(
        {'message': 'Sending started', 'campaign_id': campaign_id, 'pending': pending},
        status=status.HTTP_202_ACCEPTED,
    )


def _daily_quota_limit():
    """Reads the configured daily contact ceiling from the throttle rate."""
    rate = settings.REST_FRAMEWORK.get('DEFAULT_THROTTLE_RATES', {}).get('send_quota', '20000/day')
    try:
        return int(rate.split('/')[0])
    except (ValueError, IndexError):
        return 20_000


@api_view(['POST'])
def reset_whatsapp_session(request):
    """Forces the next send to re-link WhatsApp from scratch."""
    from .whatsapp_automation import reset_session

    WhatsAppLoginState.objects.filter(user=request.user).delete()
    try:
        reset_session(request.user.id)
    except Exception:
        logger.exception('Could not reset WhatsApp session for user %s', request.user.id)
        return Response(
            {'error': 'Could not reset the WhatsApp session.', 'code': 'reset_failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response({'message': 'WhatsApp session reset. The next send will ask for a new login code.'})
