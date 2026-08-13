import json
import os
import re
import threading

from django.conf import settings as django_settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import Campaign, Contact, MediaFile, UserProfile
from .serializers import CampaignSerializer, UserProfileSerializer
from .whatsapp_automation import send_whatsapp_messages

PHONE_RE = re.compile(r'^\+?[0-9]{10,15}$')
MAX_CONTACTS_PER_CAMPAIGN = 5000


def _login_code_path(user_id):
    """Per-user WhatsApp login-code text file; keeps login codes isolated per user."""
    return os.path.join(django_settings.MEDIA_ROOT, f'login_code_{user_id}.txt')


def _login_slow_flag_path(user_id):
    return os.path.join(django_settings.MEDIA_ROOT, f'login_slow_{user_id}.flag')


def _login_failed_flag_path(user_id):
    return os.path.join(django_settings.MEDIA_ROOT, f'login_failed_{user_id}.flag')


def _login_code_error_path(user_id):
    return os.path.join(django_settings.MEDIA_ROOT, f'login_code_error_{user_id}.txt')


@api_view(['POST'])
@permission_classes([AllowAny])
def register_user(request):
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
    username = request.data.get('username')
    password = request.data.get('password')

    user = authenticate(username=username, password=password)
    if user is None:
        return Response({'error': 'Invalid username or password'}, status=status.HTTP_401_UNAUTHORIZED)

    token, _ = Token.objects.get_or_create(user=user)
    return Response({'token': token.key, 'username': user.username})


@api_view(['GET', 'PUT'])
def whatsapp_profile(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == 'GET':
        return Response(UserProfileSerializer(profile).data)

    raw_number = str(request.data.get('whatsapp_number', '')).strip()
    cleaned = re.sub(r'[\s\-()]', '', raw_number)

    if not PHONE_RE.match(cleaned):
        return Response(
            {'error': 'That doesn\'t look like a valid phone number. Include your country code, e.g. +2348161234765.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    profile.whatsapp_number = cleaned
    profile.save()
    return Response(UserProfileSerializer(profile).data)


@api_view(['POST'])
def create_campaign(request):
    name = request.data.get('name', 'Untitled Campaign')
    message_text = request.data.get('message_text', '')
    send_mode = request.data.get('send_mode', 'delay')
    contacts_raw = request.data.get('contacts', '[]')

    try:
        contacts_list = json.loads(contacts_raw) if isinstance(contacts_raw, str) else contacts_raw
    except json.JSONDecodeError:
        return Response({'error': 'Invalid contacts format'}, status=status.HTTP_400_BAD_REQUEST)

    if not isinstance(contacts_list, list) or len(contacts_list) == 0:
        return Response({'error': 'At least one contact is required'}, status=status.HTTP_400_BAD_REQUEST)

    if len(contacts_list) > MAX_CONTACTS_PER_CAMPAIGN:
        return Response(
            {'error': f'Too many contacts — max {MAX_CONTACTS_PER_CAMPAIGN} per campaign'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    valid_contacts = []
    for contact in contacts_list:
        phone = str(contact.get('phone', '')).strip()
        cleaned = re.sub(r'[\s\-()]', '', phone)
        if PHONE_RE.match(cleaned):
            valid_contacts.append({**contact, 'phone': cleaned})

    if not valid_contacts:
        return Response(
            {'error': 'None of the contacts had a valid phone number'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    campaign = Campaign.objects.create(
        owner=request.user,
        name=name,
        message_text=message_text,
        send_mode=send_mode,
    )

    for contact in valid_contacts:
        Contact.objects.create(
            campaign=campaign,
            name=contact.get('name', ''),
            phone=contact.get('phone', ''),
        )

    files = request.FILES.getlist('media_files')
    for file_obj in files:
        MediaFile.objects.create(campaign=campaign, file=file_obj)

    serializer = CampaignSerializer(campaign)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['GET'])
def get_campaign_status(request, campaign_id):
    try:
        campaign = Campaign.objects.get(id=campaign_id, owner=request.user)
    except Campaign.DoesNotExist:
        return Response({'error': 'Campaign not found'}, status=status.HTTP_404_NOT_FOUND)

    serializer = CampaignSerializer(campaign)
    return Response(serializer.data)


@api_view(['GET'])
def get_login_status(request):
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
        with open(code_path, encoding='utf-8') as f:
            response['login_code'] = f.read().strip()
        response['code_ready'] = True

    if os.path.exists(error_path):
        with open(error_path, encoding='utf-8') as f:
            response['login_code_error'] = f.read().strip()

    return Response(response)


def _mark_login_slow(user_id):
    path = _login_slow_flag_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('slow')


def _mark_login_failed(user_id):
    path = _login_failed_flag_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('failed')


def _mark_login_code_error(user_id, error_text):
    path = _login_code_error_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(error_text)


def run_campaign_send(campaign_id, user_id):
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
        whatsapp_number=profile.whatsapp_number,
        media_paths=media_paths,
        delay_seconds=delay,
        on_progress=update_status,
        on_login_slow=lambda: _mark_login_slow(user_id),
        on_login_failed=lambda: _mark_login_failed(user_id),
        on_login_code_error=lambda err: _mark_login_code_error(user_id, err),
    )

    if os.path.exists(slow_flag):
        os.remove(slow_flag)


@api_view(['POST'])
def start_sending(request, campaign_id):
    try:
        campaign = Campaign.objects.get(id=campaign_id, owner=request.user)
    except Campaign.DoesNotExist:
        return Response({'error': 'Campaign not found'}, status=status.HTTP_404_NOT_FOUND)

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not profile.whatsapp_number:
        return Response(
            {'error': 'Add your WhatsApp phone number in settings before sending.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    thread = threading.Thread(target=run_campaign_send, args=(campaign_id, request.user.id))
    thread.start()
    return Response({'message': 'Sending started', 'campaign_id': campaign_id})
