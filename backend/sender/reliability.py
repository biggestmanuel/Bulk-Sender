"""
Detection and recovery of abandoned campaign sends.

A send runs on a background thread inside a web worker. That thread can die
in ways its own `finally` block can't handle:

  * gunicorn recycles or restarts a worker (max_requests, deploy, OOM kill)
  * the box reboots
  * the process is killed outright

In every case the campaign row is left at QUEUED or RUNNING in the database
with no thread behind it. Because those statuses are treated as "in flight",
every subsequent start attempt for that user returned 409 "already sending"
forever — with no user-facing way to recover except editing the database by
hand.

The fix is a staleness timeout. The worker refreshes a heartbeat on every
contact; anything that has gone quiet for longer than
STALE_AFTER_SECONDS is presumed dead and gets settled as failed.
"""

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Campaign, Contact

logger = logging.getLogger(__name__)


def stale_after_seconds():
    return int(getattr(settings, 'CAMPAIGN_STALE_AFTER_SECONDS', 45 * 60))


def is_stale(campaign, now=None, timeout=None):
    """
    True if this campaign claims to be in flight but nothing is moving.

    The heartbeat is the reliable signal because it advances on every contact.
    `send_started_at` is the fallback for campaigns that died between being
    queued and the first heartbeat.
    """
    if campaign.status not in {Campaign.Status.QUEUED, Campaign.Status.RUNNING}:
        return False

    now = now or timezone.now()
    cutoff_seconds = timeout if timeout is not None else stale_after_seconds()

    reference = campaign.last_heartbeat_at or campaign.send_started_at
    if reference is None:
        # Queued but never even stamped a start time. If it has been sitting
        # here long enough for the thread to have died, treat it as stale.
        reference = campaign.created_at

    age = (now - reference).total_seconds()
    return age > cutoff_seconds


@transaction.atomic
def reap_stale_campaigns(owner=None, now=None, timeout=None):
    """
    Marks abandoned in-flight campaigns as failed and returns their ids.

    Safe to call concurrently from several workers: the UPDATE is filtered on
    the status still being in flight, so only one caller wins per row.
    """
    now = now or timezone.now()
    cutoff_seconds = timeout if timeout is not None else stale_after_seconds()

    queryset = Campaign.objects.filter(
        status__in=[Campaign.Status.QUEUED, Campaign.Status.RUNNING]
    )
    if owner is not None:
        queryset = queryset.filter(owner=owner)

    reaped = []
    # The .only() is deliberate — message_text is encrypted, so selecting the
    # full row would decrypt every campaign body just to test a timestamp.
    # But it has to name every column is_stale() touches, or Django defers them
    # and issues a fresh SELECT per campaign for last_heartbeat_at (and again
    # for send_started_at / created_at when the heartbeat is null). Measured at
    # one extra query per stale campaign; listing the columns makes it one
    # query total.
    for campaign in queryset.select_related('owner').only(
        'id',
        'owner_id',
        'status',
        'last_heartbeat_at',
        'send_started_at',
        'created_at',
    ):
        if not is_stale(campaign, now=now, timeout=cutoff_seconds):
            continue

        # Contact rows still marked pending were never attempted.
        Contact.objects.filter(campaign=campaign, status='pending').update(status='failed')

        updated = Campaign.objects.filter(
            id=campaign.pk,
            status__in=[Campaign.Status.QUEUED, Campaign.Status.RUNNING],
        ).update(
            status=Campaign.Status.FAILED,
            status_detail='The send stopped unexpectedly and was marked as failed. '
                          'Use "Retry failed" to try again.',
            send_finished_at=now,
        )

        if updated:
            reaped.append(campaign.pk)
            logger.warning(
                'Campaign %s (user %s) was %s with no heartbeat for over %ds; marked failed.',
                campaign.pk, campaign.owner_id, campaign.status, cutoff_seconds,
            )

    return reaped
