"""
Campaign listing and retry.

Two capabilities that were missing entirely:

* **Listing.** Campaigns were only reachable from the progress view of the
  session that created them. Reloading the page, or coming back tomorrow, and
  there was no way to find a previous campaign at all.

* **Retry.** A campaign that failed halfway through left every unsent contact
  stranded at 'failed' with no way to finish them. Because Contact status is
  already per-row, retry is mostly a matter of flipping those rows back to
  'pending'.
"""

import logging

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.response import Response

from .models import Campaign, Contact
from .reliability import reap_stale_campaigns
from .serializers import CampaignListSerializer
from .throttles import CampaignCreateThrottle


logger = logging.getLogger(__name__)

# Enough history to be useful without making the response unbounded. A user
# with 10,000 campaigns only ever needs the recent ones.
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


def _pagination_params(request):
    def positive_int(name, default):
        raw = request.GET.get(name)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    page_size = min(positive_int('page_size', DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE)
    return positive_int('page', 1), page_size


@api_view(['GET'])
@throttle_classes([CampaignCreateThrottle])
def list_campaigns(request):
    """
    The caller's campaigns, newest first, with progress counts.

    Counts come from a single grouped query with conditional aggregation
    rather than loading every contact. With 5,000 contacts across many
    campaigns, the naive version decrypted the entire contact table.

    Also reaps stale sends before listing, so a campaign orphaned by a dead
    worker shows as failed rather than "sending" indefinitely.
    """
    page, page_size = _pagination_params(request)

    reap_stale_campaigns(owner=request.user)

    queryset = (
        Campaign.objects.filter(owner=request.user)
        .annotate(
            total_contacts=Count('contacts', distinct=True),
            sent_contacts=Count('contacts', filter=Q(contacts__status='sent')),
            failed_contacts=Count('contacts', filter=Q(contacts__status='failed')),
        )
        # Prefetched because CampaignListSerializer reports the media count;
        # without this it fires a query per campaign.
        .prefetch_related('media_files')
        .order_by('-created_at')
    )

    total = queryset.count()
    offset = (page - 1) * page_size
    campaigns = list(queryset[offset:offset + page_size])

    has_next = offset + page_size < total

    return Response({
        'campaigns': CampaignListSerializer(campaigns, many=True).data,
        'page': page,
        'page_size': page_size,
        'total': total,
        'has_next': has_next,
    })


@api_view(['POST'])
def retry_campaign(request, campaign_id):
    """
    Returns failed contacts to pending so the campaign can be sent again.

    Deliberately does NOT restart the send itself. That would couple a cheap
    state fix to an expensive operation, and the caller would have no way to
    see the outcome. It returns the count, and the UI calls /start/ separately
    so the existing guards (already sending, quota) still apply.
    """
    try:
        campaign = Campaign.objects.get(id=campaign_id, owner=request.user)
    except Campaign.DoesNotExist:
        return Response(
            {'error': 'Campaign not found.', 'code': 'not_found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    # A worker that died mid-send must be reaped before we start rewriting its
    # contact rows, otherwise the live worker and this request race over the
    # same rows.
    reap_stale_campaigns(owner=request.user)

    campaign.refresh_from_db()

    if campaign.status in Campaign.IN_FLIGHT_STATUSES:
        return Response(
            {'error': 'This campaign is still sending.', 'code': 'already_sending'},
            status=status.HTTP_409_CONFLICT,
        )

    with transaction.atomic():
        reset = Contact.objects.filter(campaign=campaign, status='failed').update(
            status='pending'
        )
        campaign.status = Campaign.Status.DRAFT
        campaign.status_detail = ''
        campaign.send_started_at = None
        campaign.send_finished_at = None
        campaign.last_heartbeat_at = timezone.now()
        campaign.save(update_fields=[
            'status', 'status_detail', 'send_started_at', 'send_finished_at',
            'last_heartbeat_at',
        ])

    if reset:
        logger.info(
            'Campaign %s: reset %s failed contact(s) to pending for retry',
            campaign_id, reset,
        )

    return Response({
        'message': (
            f'{reset} contact(s) are ready to send again.'
            if reset
            else 'There were no failed contacts to retry.'
        ),
        'reset_count': reset,
        'campaign_id': campaign_id,
        'status': campaign.status,
    })


@api_view(['DELETE'])
def delete_campaign(request, campaign_id):
    """
    Deletes a campaign and its contacts and attachments.

    Used by the campaign list's remove action. MediaFile rows are removed
    explicitly because the underlying files live on disk and would otherwise
    be orphaned in MEDIA_ROOT forever.
    """
    try:
        campaign = Campaign.objects.get(id=campaign_id, owner=request.user)
    except Campaign.DoesNotExist:
        return Response(
            {'error': 'Campaign not found.', 'code': 'not_found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Reap first. Without this, a campaign orphaned by a dead worker stays
    # 'running' until something else triggers recovery, and every delete
    # attempt returns 409 — the same permanent lockout that retry_campaign and
    # start_sending already guard against.
    reap_stale_campaigns(owner=request.user)
    campaign.refresh_from_db()

    if campaign.status in Campaign.IN_FLIGHT_STATUSES:
        return Response(
            {
                'error': 'This campaign is still sending. Wait for it to finish before deleting it.',
                'code': 'already_sending',
            },
            status=status.HTTP_409_CONFLICT,
        )

    removed_files = []
    for media in campaign.media_files.all():
        try:
            removed_files.append(media.file.name)
        except Exception:
            logger.exception('Could not resolve media path for %s', media.pk)

    with transaction.atomic():
        campaign.delete()  # cascades to contacts and media rows

    # Storage cleanup runs after the transaction commits so a rolled-back
    # delete can't leave the database pointing at files that no longer exist.
    storage = _media_storage()
    for name in removed_files:
        try:
            storage.delete(name)
        except Exception:
            logger.exception('Could not delete media file %s', name)

    return Response({'message': 'Campaign deleted.', 'campaign_id': campaign_id})


def _media_storage():
    from django.core.files.storage import default_storage

    return default_storage