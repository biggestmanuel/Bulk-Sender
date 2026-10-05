import logging

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Count

from .encryption import EncryptedCharField, EncryptedTextField

logger = logging.getLogger(__name__)


class Campaign(models.Model):
    class Meta:
        indexes = [
            # The campaign list filters by owner and orders newest-first. With
            # an index on owner_id alone, SQLite sorts the whole result set in
            # a temp B-tree on every request; EXPLAIN confirmed
            # "USE TEMP B-TREE FOR ORDER BY". Harmless at a handful of rows,
            # degrading linearly as a user's history grows.
            models.Index(fields=['owner', '-created_at'], name='campaign_owner_created_idx'),
            # Stale-send recovery scans for in-flight campaigns regardless of
            # owner, so a plain status index is what's needed there.
            models.Index(fields=['status'], name='campaign_status_idx'),
        ]

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        QUEUED = 'queued', 'Queued'
        RUNNING = 'running', 'Running'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'

    #: Campaign in one of these states is assumed to have a live worker.
    IN_FLIGHT_STATUSES = (Status.QUEUED, Status.RUNNING)

    class SendMode(models.TextChoices):
        DELAY = 'delay', 'Delay'
        INSTANT = 'instant', 'Instant'

    # Every campaign belongs to a specific user. This used to be
    # null=True/blank=True to avoid breaking pre-auth test data — that
    # transition is done now (see migration 0003), so this is required.
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='campaigns')

    name = models.CharField(max_length=255)
    message_text = EncryptedTextField()
    send_mode = models.CharField(
        max_length=10,
        choices=SendMode.choices,
        default=SendMode.DELAY,
    )

    # Send lifecycle. Previously "is this campaign running?" was inferred from
    # whether any contact still had status='pending', which cannot distinguish
    # a send that never started from one that is still waiting on a phone
    # login, and cannot distinguish "running" from "crashed halfway".
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    status_detail = models.CharField(max_length=255, blank=True, default='')
    send_started_at = models.DateTimeField(null=True, blank=True)
    send_finished_at = models.DateTimeField(null=True, blank=True)

    # Refreshed by the worker on every contact. This is the signal
    # sender/reliability.py uses to tell a live send from one whose worker
    # died with gunicorn recycling a process or the box rebooting.
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    @property
    def is_finished(self):
        return self.status in {self.Status.COMPLETED, self.Status.FAILED}

    def progress(self):
        """
        Returns {total, sent, failed, pending} for this campaign.

        This does one grouped query rather than pulling every contact into
        Python: the status endpoint is polled every 3 seconds by the
        frontend, and with 5,000 contacts the old per-contact loop meant
        tens of thousands of decrypted rows per poll, per user, per minute.
        """
        counts = dict(
            self.contacts.values('status')
            .annotate(count=Count('id'))
            .values_list('status', 'count')
        )
        total = sum(counts.values())
        return {
            'total': total,
            'sent': counts.get('sent', 0),
            'failed': counts.get('failed', 0),
            'pending': counts.get('pending', 0),
        }


class Contact(models.Model):
    class Meta:
        indexes = [
            # Campaign.progress() groups by status for one campaign, and the
            # send worker fetches pending contacts for one campaign. Both are
            # run every 3 seconds per active send, so the status column is
            # worth carrying in the index rather than grouping over every
            # contact in the table.
            models.Index(fields=['campaign', 'status'], name='contact_campaign_status_idx'),
        ]

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name='contacts')
    name = models.CharField(max_length=255)
    phone = EncryptedCharField(max_length=20)
    status = models.CharField(
        max_length=10,
        choices=[('pending', 'Pending'), ('sent', 'Sent'), ('failed', 'Failed')],
        default='pending',
    )

    def __str__(self):
        return f"{self.name} ({self.phone})"


class MediaFile(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name='media_files')
    file = models.FileField(upload_to='campaign_media/')

    def __str__(self):
        return self.file.name


class UserProfile(models.Model):
    """
    Holds the WhatsApp phone number each user links their account with,
    used for the "log in with phone number" flow instead of QR scanning.
    Separate model (rather than extending User directly) so it can grow
    later — send preferences, etc. — without touching auth.

    whatsapp_number starts blank; a user must set it via
    GET/PUT /api/profile/whatsapp-number/ before they can start a send
    (enforced in views.start_sending).
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    whatsapp_number = EncryptedCharField(max_length=20, blank=True, default='')

    def __str__(self):
        return f"Profile for {self.user.username}"


class WhatsAppLoginState(models.Model):
    """
    Live handshake state for one user's Playwright WhatsApp Web session.

    This used to be five files in MEDIA_ROOT (login_code_<id>.txt,
    login_slow_<id>.flag, login_failed_<id>.flag, login_code_error_<id>.txt,
    debug screenshots). That was fine when the whole thing ran as one local
    process, but it breaks in two ways that matter:

      1. gunicorn has multiple workers, so the HTTP request that polls for
         the login code and the Playwright thread that writes it can land in
         different processes. A file works; this also works, and works
         identically across workers because it is in the database.
      2. Every PaaS host (Render included, per the deployment notes in the
         README) has an *ephemeral* filesystem. The session profile, the
         login code and the debug screenshots all vanish on every restart
         and every deploy.

    The browser session directory itself cannot move into the database —
    Chromium needs a real directory for its profile — so that stays on
    disk, but everything the HTTP layer needs to read now lives here.

    Rows are one-per-user (OneToOne), so state can never leak between two
    people sending at the same time.
    """

    class Phase(models.TextChoices):
        IDLE = 'idle', 'Idle'
        WAITING_FOR_CODE = 'waiting_for_code', 'Waiting for code'
        AWAITING_LOGIN = 'awaiting_login', 'Awaiting phone confirmation'
        LOGGED_IN = 'logged_in', 'Logged in'
        SLOW = 'slow', 'Slow network'
        FAILED = 'failed', 'Failed'

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='whatsapp_login_state')
    phase = models.CharField(max_length=20, choices=Phase.choices, default=Phase.IDLE)

    # The 8-character code WhatsApp Web shows in the browser. Plaintext is
    # fine here: it is only useful for the few minutes between "code
    # generated" and "linked", and the database row is already protected by
    # the fact that the whole DB is the thing at risk. Encrypting it would
    # buy nothing against an attacker who can read the row.
    login_code = models.CharField(max_length=32, blank=True, default='')
    code_generated_at = models.DateTimeField(null=True, blank=True)

    # Populated when the automation could not produce a code at all (selector
    # miss, WhatsApp markup change, network failure). Surfaced in the UI so
    # the overlay doesn't sit blank forever.
    error = models.TextField(blank=True, default='')

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"WhatsApp login state for {self.user.username}: {self.phase}"

    def reset(self, phase=Phase.IDLE):
        """Clears any previous handshake before a new send begins."""
        self.phase = phase
        self.login_code = ''
        self.code_generated_at = None
        self.error = ''
        self.save(update_fields=['phase', 'login_code', 'code_generated_at', 'error', 'updated_at'])


class DailySendQuota(models.Model):
    """
    Rolling counter of contacts queued per user, so one account can't park
    the browser on its session and push an unbounded number of messages.

    The previous code only had MAX_CONTACTS_PER_CAMPAIGN (5,000) — a
    per-request ceiling that did nothing to stop the same user creating
    campaign after campaign all day.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='send_quota')
    date = models.DateField()
    contacts_used = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name_plural = 'daily send quotas'

    def __str__(self):
        return f"{self.user.username}: {self.contacts_used} contacts on {self.date}"

    @classmethod
    def reserve(cls, user, count, limit):
        """
        Atomically reserves `count` contacts against today's quota.

        Uses a conditional UPDATE so two concurrent sends can't both read the
        same current count and both believe they fit under the limit. Returns
        the new total, or None if the reservation would exceed `limit`.
        """
        from django.db import IntegrityError, transaction
        from django.db.models import F
        from django.utils import timezone

        today = timezone.localdate()

        try:
            with transaction.atomic():
                quota, _created = cls.objects.get_or_create(
                    user=user,
                    defaults={'date': today, 'contacts_used': 0},
                )
                # A stale row from a previous day resets rather than
                # accumulating forever.
                if quota.date != today:
                    cls.objects.filter(pk=quota.pk).update(date=today, contacts_used=0)

                updated = cls.objects.filter(pk=quota.pk, date=today).update(
                    contacts_used=F('contacts_used') + count
                )
                if not updated:
                    return None

                quota.refresh_from_db()
                if quota.contacts_used > limit:
                    # Roll the reservation back so a rejected attempt doesn't
                    # eat into the user's real allowance.
                    cls.objects.filter(pk=quota.pk).update(
                        contacts_used=F('contacts_used') - count
                    )
                    return None
                return quota.contacts_used
        except IntegrityError:
            # get_or_create raced with another request; treat as "no room".
            logger.warning('Quota reservation raced for user %s', user.pk)
            return None
