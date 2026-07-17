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
from .models import Campaign, Contact, MediaFile
from .serializers import CampaignSerializer

PHONE_RE = re.compile(r'^\+?[0-9]{10,15}$')
MAX_CONTACTS_PER_CAMPAIGN = 5000


def _qr_path(user_id):
    """Per-user QR screenshot path — keeps two users' logins from colliding."""
    return os.path.join(django_settings.MEDIA_ROOT, f'qr_code_{user_id}.png')


def _qr_slow_flag_path(user_id):
    return os.path.join(django_settings.MEDIA_ROOT, f'qr_slow_{user_id}.flag')


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
            valid_contacts.append(contact)

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
def get_qr_status(request):
    """
    Frontend polls this while a campaign is starting up, to know whether
    a WhatsApp login QR code is currently available to scan, and whether
    the wait has been going on long enough to warn about a slow network.

    QR/session state is now per-user (keyed off request.user.id), so two
    people starting a send around the same time no longer see or
    overwrite each other's QR code or WhatsApp session.
    """
    user_id = request.user.id
    qr_ready = os.path.exists(_qr_path(user_id))
    qr_slow = os.path.exists(_qr_slow_flag_path(user_id))

    response = {'qr_ready': qr_ready, 'qr_slow': qr_slow}
    if qr_ready:
        response['qr_url'] = f'/media/qr_code_{user_id}.png'

    return Response(response)


def _mark_qr_slow(user_id):
    """Called from the automation script if login is taking a long time."""
    path = _qr_slow_flag_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write('slow')


def run_campaign_send(campaign_id, user_id):
    """
    This runs in a background thread — pulls campaign data and starts sending.

    Takes user_id explicitly (passed in by start_sending, which already
    verified ownership) rather than trusting campaign_id alone. The lookup
    below re-checks owner_id so a campaign can never be sent using the
    wrong user's WhatsApp session, even if this function is ever called
    from somewhere else in the future.
    """
    slow_flag = _qr_slow_flag_path(user_id)
    if os.path.exists(slow_flag):
        os.remove(slow_flag)

    try:
        campaign = Campaign.objects.get(id=campaign_id, owner_id=user_id)
    except Campaign.DoesNotExist:
        return

    contacts = list(campaign.contacts.filter(status='pending').values('id', 'name', 'phone'))
    media_paths = [m.file.path for m in campaign.media_files.all()]
    delay = 3 if campaign.send_mode == 'delay' else 1

    def update_status(contact_id, contact_status):
        Contact.objects.filter(id=contact_id).update(status=contact_status)

    send_whatsapp_messages(
        contacts=contacts,
        message_text=campaign.message_text,
        user_id=user_id,
        media_paths=media_paths,
        delay_seconds=delay,
        on_progress=update_status,
        on_qr_slow=lambda: _mark_qr_slow(user_id)
    )

    if os.path.exists(slow_flag):
        os.remove(slow_flag)


@api_view(['POST'])
def start_sending(request, campaign_id):
    """
    Kicks off the sending process in a background thread so the API responds immediately.
    Only allows this if the requesting user owns the campaign.
    """
    try:
        campaign = Campaign.objects.get(id=campaign_id, owner=request.user)
    except Campaign.DoesNotExist:
        return Response({'error': 'Campaign not found'}, status=status.HTTP_404_NOT_FOUND)

    thread = threading.Thread(target=run_campaign_send, args=(campaign_id, request.user.id))
    thread.start()

    return Response({'message': 'Sending started', 'campaign_id': campaign_id})