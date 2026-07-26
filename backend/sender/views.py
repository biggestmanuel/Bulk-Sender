import os
import re
import threading
from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from .whatsapp_automation import send_whatsapp_messages
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework import status
from .models import Campaign, Contact, MediaFile, UserProfile
from .serializers import CampaignSerializer, UserProfileSerializer

PHONE_RE = re.compile(r'^\+?[0-9]{10,15}$')
MAX_CONTACTS_PER_CAMPAIGN = 5000


def _login_code_path(user_id):
    """Per-user WhatsApp login-code text file — keeps two users' logins
    from colliding, same idea as the old per-user QR screenshot path."""
    return os.path.join(django_settings.MEDIA_ROOT, f'login_code_{user_id}.txt')


def _login_slow_flag_path(user_id):
    return os.path.join(django_settings.MEDIA_ROOT, f'login_slow_{user_id}.flag')


def _login_failed_flag_path(user_id):
    """Set when the automation script gives up entirely (page load
    failure or the 10-minute login timeout). Without this, the frontend
    had no way to distinguish 'still waiting' from 'gave up' once no
    code had ever been shown, so it polled forever."""
    return os.path.join(django_settings.MEDIA_ROOT, f'login_failed_{user_id}.flag')


def _login_code_error_path(user_id):
    """Written if the automation script fails to generate a login code at
    all (e.g. a Playwright selector didn't match because the page hadn't
    finished loading). Previously this failure was console/screenshot-only
    — the frontend had no signal and just showed a blank overlay until the
    unrelated 10-minute timeout eventually fired."""
    return os.path.join(django_settings.MEDIA_ROOT, f'login_code_error_{user_id}.txt')


# --- AUTH ---

@api_view(['POST'])
@permission_classes([AllowAny])
def register_user(request):
    """
    Creates a new user account. Expects: username, password, email (optional).
    Returns an auth token the frontend should store and send with every
    future request (so we know whose campaigns belong to whom).
    """
    username = request.data.get('username')
    password = request.data.get('password')
    email = request.data.get('email', '')

    if not username or not password:
        return Response({'error': 'Username and password are required'}, status=status.HTTP_400_BAD_REQUEST)

    if User.objects.filter(username=username).exists():
        return Response({'error': 'Username already taken'}, status=status.HTTP_400_BAD_REQUEST)

    user = User.objects.create_user(username=username, password=password, email=email)
    token, _ = Token.objects.get_or_create(user=user)

    return Response({'token': token.key, 'username': user.username}, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([AllowAny])
def login_user(request):
    """
    Logs in an existing user. Expects: username, password.
    Returns the same auth token every time (doesn't create a new one per login).
    """
    username = request.data.get('username')
    password = request.data.get('password')

    user = authenticate(username=username, password=password)
    if user is None:
        return Response({'error': 'Invalid username or password'}, status=status.HTTP_401_UNAUTHORIZED)

    token, _ = Token.objects.get_or_create(user=user)
    return Response({'token': token.key, 'username': user.username})


# --- PROFILE (WhatsApp phone number used for the "log in with phone
# number" flow, editable anytime, not just on first setup) ---

@api_view(['GET', 'PUT'])
def whatsapp_profile(request):
    """
    GET returns the current WhatsApp number on file (blank string if
    never set). PUT updates it — validated with the same phone regex
    used for contacts, since this number needs to actually work with
    WhatsApp's phone-number login flow.
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == 'GET':
        return Response(UserProfileSerializer(profile).data)

    raw_number = str(request.data.get('whatsapp_number', '')).strip()
    cleaned = re.sub(r'[\s\-()]', '', raw_number)

    if not PHONE_RE.match(cleaned):
        return Response(
            {'error': 'That doesn\'t look like a valid phone number. Include your country code, e.g. +2348161234765.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    profile.whatsapp_number = cleaned
    profile.save()
    return Response(UserProfileSerializer(profile).data)


# --- CAMPAIGNS (all scoped to the logged-in user) ---

@api_view(['POST'])
def create_campaign(request):
    """
    Creates a new campaign with contacts and optional media files,
    owned by whichever user is making the request.
    Expects: name, message_text, send_mode, contacts (list of {name, phone}), media files

    Now validates on the server too (not just in the UI): caps the number
    of contacts per campaign, and drops any contact whose phone number
    doesn't look valid rather than trusting the payload blindly.
    """
    name = request.data.get('name', 'Untitled Campaign')
    message_text = request.data.get('message_text', '')
    send_mode = request.data.get('send_mode', 'delay')
    contacts_raw = request.data.get('contacts', '[]')

    import json
    try:
        contacts_list = json.loads(contacts_raw) if isinstance(contacts_raw, str) else contacts_raw
    except json.JSONDecodeError:
        return Response({'error': 'Invalid contacts format'}, status=status.HTTP_400_BAD_REQUEST)

    if not isinstance(contacts_list, list) or len(contacts_list) == 0:
        return Response({'error': 'At least one contact is required'}, status=status.HTTP_400_BAD_REQUEST)

    if len(contacts_list) > MAX_CONTACTS_PER_CAMPAIGN:
        return Response(
            {'error': f'Too many contacts — max {MAX_CONTACTS_PER_CAMPAIGN} per campaign'},
            status=status.HTTP_400_BAD_REQUEST
        )

    valid_contacts = []
    for contact in contacts_list:
        phone = str(contact.get('phone', '')).strip()
        cleaned = re.sub(r'[\s\-()]', '', phone)
        if PHONE_RE.match(cleaned):
            # Store the cleaned version, not the raw input — this is what
            # gets matched against later and what whatsapp_automation.py
            # expects (it does its own light stripping of '+'/' ' but
            # shouldn't have to also handle dashes/parens/etc left over
            # from a messy CSV).
            valid_contacts.append({**contact, 'phone': cleaned})

    if not valid_contacts:
        return Response(
            {'error': 'None of the contacts had a valid phone number'},
            status=status.HTTP_400_BAD_REQUEST
        )

    campaign = Campaign.objects.create(
        owner=request.user,
        name=name,
        message_text=message_text,
        send_mode=send_mode
    )

    for contact in valid_contacts:
        Contact.objects.create(
            campaign=campaign,
            name=contact.get('name', ''),
            phone=contact.get('phone', '')
        )

    files = request.FILES.getlist('media_files')
    for f in files:
        MediaFile.objects.create(campaign=campaign, file=f)

    serializer = CampaignSerializer(campaign)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['GET'])
def get_campaign_status(request, campaign_id):
    """
    Returns the current status of a campaign — used to poll progress.
    Only returns it if the requesting user actually owns this campaign.
    """
    try:
        campaign = Campaign.objects.get(id=campaign_id, owner=request.user)
    except Campaign.DoesNotExist:
        return Response({'error': 'Campaign not found'}, status=status.HTTP_404_NOT_FOUND)

    serializer = CampaignSerializer(campaign)
    return Response(serializer.data)


@api_view(['GET'])
def get_login_status(request):
    """
    Frontend polls this while a campaign is starting up, to know whether
    a WhatsApp login code is currently available to enter on the phone,
    whether the wait has been going on long enough to warn about a slow
    network, and whether the automation script gave up entirely.

    login_failed is what actually stops the frontend from polling
    forever: without it, if a code was never captured in the first place
    (e.g. the phone-number-login click/selectors failed), the frontend
    had no signal to distinguish "still waiting" from "already gave up
    10 minutes ago" and would just keep polling indefinitely.
    """
    user_id = request.user.id
    code_path = _login_code_path(user_id)
    login_slow = os.path.exists(_login_slow_flag_path(user_id))
    login_failed = os.path.exists(_login_failed_flag_path(user_id))
    error_path = _login_code_error_path(user_id)

    response = {
        'code_ready': False,
        'login_code': None,
        'login_slow': login_slow,
        'login_failed': login_failed,
        'login_code_error': None,
    }
    if os.path.exists(code_path):
        with open(code_path) as f:
            response['login_code'] = f.read().strip()
        response['code_ready'] = True

    if os.path.exists(error_path):
        with open(error_path) as f:
            response['login_code_error'] = f.read().strip()

    return Response(response)


def _mark_login_slow(user_id):
    """Called from the automation script if login is taking a long time."""
    path = _login_slow_flag_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write('slow')


def _mark_login_failed(user_id):
    """Called from the automation script when it gives up entirely —
    page failed to load, or the 10-minute login timeout was hit."""
    path = _login_failed_flag_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write('failed')


def _mark_login_code_error(user_id, error_text):
    """Called from the automation script if it couldn't generate a login
    code at all. This does NOT stop the send — the script still moves on
    to wait for the chat list (in case the code did appear but couldn't be
    scraped) — it just gives the frontend something to show instead of a
    silent blank overlay while that continues."""
    path = _login_code_error_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(error_text)


def run_campaign_send(campaign_id, user_id):
    """
    This runs in a background thread — pulls campaign data and starts sending.

    Takes user_id explicitly (passed in by start_sending, which already
    verified ownership) rather than trusting campaign_id alone. The lookup
    below re-checks owner_id so a campaign can never be sent using the
    wrong user's WhatsApp session, even if this function is ever called
    from somewhere else in the future.
    """
    slow_flag = _login_slow_flag_path(user_id)
    if os.path.exists(slow_flag):
        os.remove(slow_flag)

    failed_flag = _login_failed_flag_path(user_id)
    if os.path.exists(failed_flag):
        os.remove(failed_flag)

    error_path = _login_code_error_path(user_id)
    if os.path.exists(error_path):
        os.remove(error_path)

    try:
        campaign = Campaign.objects.get(id=campaign_id, owner_id=user_id)
    except Campaign.DoesNotExist:
        return

    try:
        profile = UserProfile.objects.get(user_id=user_id)
    except UserProfile.DoesNotExist:
        # start_sending already checks this before spawning the thread,
        # so this should only happen if the profile got deleted mid-flight.
        return

    contacts = list(campaign.contacts.filter(status='pending').values('id', 'name', 'phone'))
    media_paths = [m.file.path for m in campaign.media_files.all()]
    # This is no longer the actual gap used in delay mode — that's now a
    # randomized 5-15s pace with occasional longer breaks, computed inside
    # send_whatsapp_messages. This value only distinguishes delay (any
    # value other than 1) from instant (1s flat, unchanged).
    delay = 3 if campaign.send_mode == 'delay' else 1

    def update_status(contact_id, contact_status):
        Contact.objects.filter(id=contact_id).update(status=contact_status)

    send_whatsapp_messages(
        contacts=contacts,
        message_text=campaign.message_text,
        user_id=user_id,
        whatsapp_number=profile.whatsapp_number,
        media_paths=media_paths,
        delay_seconds=delay,
        on_progress=update_status,
        on_login_slow=lambda: _mark_login_slow(user_id),
        on_login_failed=lambda: _mark_login_failed(user_id),
        on_login_code_error=lambda err: _mark_login_code_error(user_id, err)
    )

    if os.path.exists(slow_flag):
        os.remove(slow_flag)


@api_view(['POST'])
def start_sending(request, campaign_id):
    """
    Kicks off the sending process in a background thread so the API responds immediately.
    Only allows this if the requesting user owns the campaign, and only
    if they've set a WhatsApp number in their profile — without one there's
    nothing to enter into the login-code flow, so it's better to catch
    that here than have the automation script fail 30+ seconds in.
    """
    try:
        campaign = Campaign.objects.get(id=campaign_id, owner=request.user)
    except Campaign.DoesNotExist:
        return Response({'error': 'Campaign not found'}, status=status.HTTP_404_NOT_FOUND)

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not profile.whatsapp_number:
        return Response(
            {'error': 'Add your WhatsApp phone number in settings before sending.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    thread = threading.Thread(target=run_campaign_send, args=(campaign_id, request.user.id))
    thread.start()

    return Response({'message': 'Sending started', 'campaign_id': campaign_id})