import threading
from .whatsapp_automation import send_whatsapp_messages
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from .models import Campaign, Contact, MediaFile
from .serializers import CampaignSerializer


@api_view(['POST'])
def create_campaign(request):
    """
    Creates a new campaign with contacts and optional media files.
    Expects: name, message_text, send_mode, contacts (list of {name, phone}), media files
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

    campaign = Campaign.objects.create(
        name=name,
        message_text=message_text,
        send_mode=send_mode
    )

    for contact in contacts_list:
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
    """
    try:
        campaign = Campaign.objects.get(id=campaign_id)
    except Campaign.DoesNotExist:
        return Response({'error': 'Campaign not found'}, status=status.HTTP_404_NOT_FOUND)

    serializer = CampaignSerializer(campaign)
    return Response(serializer.data)

def run_campaign_send(campaign_id):
    """
    This runs in a background thread — pulls campaign data and starts sending.
    """
    try:
        campaign = Campaign.objects.get(id=campaign_id)
    except Campaign.DoesNotExist:
        return

    contacts = list(campaign.contacts.filter(status='pending').values('id', 'name', 'phone'))
    media_paths = [m.file.path for m in campaign.media_files.all()]
    delay = 3 if campaign.send_mode == 'delay' else 1

    def update_status(contact_id, status):
        Contact.objects.filter(id=contact_id).update(status=status)

    send_whatsapp_messages(
        contacts=contacts,
        message_text=campaign.message_text,
        media_paths=media_paths,
        delay_seconds=delay,
        on_progress=update_status
    )


@api_view(['POST'])
def start_sending(request, campaign_id):
    """
    Kicks off the sending process in a background thread so the API responds immediately.
    """
    try:
        campaign = Campaign.objects.get(id=campaign_id)
    except Campaign.DoesNotExist:
        return Response({'error': 'Campaign not found'}, status=status.HTTP_404_NOT_FOUND)

    thread = threading.Thread(target=run_campaign_send, args=(campaign_id,))
    thread.start()

    return Response({'message': 'Sending started', 'campaign_id': campaign_id})