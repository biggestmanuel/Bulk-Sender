"""
Tests for stale-campaign detection and recovery.

This covers the failure mode that had no user-facing recovery: a send thread
dying with gunicorn recycling a worker or the box rebooting, leaving the
campaign at RUNNING and blocking that user from starting anything ever again.
"""

import io
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from sender.models import Campaign, Contact, UserProfile
from sender.reliability import is_stale, reap_stale_campaigns, stale_after_seconds

STALE = 45 * 60


class StaleAfterSettingTests(TestCase):
    def test_default(self):
        self.assertEqual(stale_after_seconds(), 45 * 60)

    def test_overridable(self):
        with override_settings(CAMPAIGN_STALE_AFTER_SECONDS=90):
            self.assertEqual(stale_after_seconds(), 90)


class IsStaleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw')
        self.campaign = Campaign.objects.create(
            owner=self.user, name='c', message_text='hi', send_mode='delay'
        )

    def test_terminal_campaigns_are_never_stale(self):
        """A finished campaign sitting in the database for months is fine."""
        for status in (Campaign.Status.DRAFT, Campaign.Status.COMPLETED, Campaign.Status.FAILED):
            self.campaign.status = status
            self.assertFalse(is_stale(self.campaign), status)

    def test_running_with_recent_heartbeat_is_not_stale(self):
        self.campaign.status = Campaign.Status.RUNNING
        self.campaign.last_heartbeat_at = timezone.now()
        self.assertFalse(is_stale(self.campaign))

    def test_running_with_old_heartbeat_is_stale(self):
        self.campaign.status = Campaign.Status.RUNNING
        self.campaign.last_heartbeat_at = timezone.now() - timedelta(seconds=STALE + 60)
        self.assertTrue(is_stale(self.campaign))

    def test_falls_back_to_send_started_at_when_no_heartbeat(self):
        """
        A worker that died before the first contact never wrote a heartbeat,
        so send_started_at is the only evidence of life.
        """
        self.campaign.status = Campaign.Status.RUNNING
        self.campaign.last_heartbeat_at = None
        self.campaign.send_started_at = timezone.now() - timedelta(seconds=STALE + 60)
        self.assertTrue(is_stale(self.campaign))

    def test_falls_back_to_created_at_when_never_started(self):
        """
        Queued but never stamped: if it has sat here longer than the timeout,
        the thread that was going to pick it up is gone.
        """
        self.campaign.status = Campaign.Status.QUEUED
        self.campaign.last_heartbeat_at = None
        self.campaign.send_started_at = None
        self.assertFalse(is_stale(self.campaign))  # just created

        self.campaign.created_at = timezone.now() - timedelta(seconds=STALE + 60)
        self.assertTrue(is_stale(self.campaign))

    def test_queued_with_fresh_heartbeat_is_not_stale(self):
        self.campaign.status = Campaign.Status.QUEUED
        self.campaign.last_heartbeat_at = timezone.now()
        self.assertFalse(is_stale(self.campaign))

    def test_custom_timeout_is_honoured(self):
        self.campaign.status = Campaign.Status.RUNNING
        self.campaign.last_heartbeat_at = timezone.now() - timedelta(seconds=120)
        self.assertTrue(is_stale(self.campaign, timeout=60))
        self.assertFalse(is_stale(self.campaign, timeout=600))


class ReapCommandTests(TestCase):
    """
    The management command wraps reap_stale_campaigns() for cron use. The
    recovery logic itself is tested above; what's checked here is that the
    command reports honestly and that --dry-run really doesn't mutate.
    """

    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw')

    def make_stale(self):
        campaign = Campaign.objects.create(
            owner=self.user, name='c', message_text='hi', send_mode='delay',
            status=Campaign.Status.RUNNING,
        )
        stamp = timezone.now() - timedelta(seconds=STALE + 300)
        Campaign.objects.filter(pk=campaign.pk).update(
            send_started_at=stamp, last_heartbeat_at=stamp
        )
        return campaign

    def call(self, **kwargs):
        out = io.StringIO()
        call_command('reap_stale_campaigns', stdout=out, stderr=out, **kwargs)
        return out.getvalue()

    def test_dry_run_reports_but_changes_nothing(self):
        campaign = self.make_stale()

        output = self.call(**{'dry_run': True})

        self.assertIn(str(campaign.pk), output)
        self.assertIn('would be reaped', output)
        self.assertIn('dry run', output)

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.RUNNING)

    def test_applies_the_fix_by_default(self):
        campaign = self.make_stale()

        output = self.call()

        self.assertIn('reaped', output)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.FAILED)

    def test_reports_when_nothing_is_stale(self):
        campaign = Campaign.objects.create(
            owner=self.user, name='c', message_text='hi', send_mode='delay',
            status=Campaign.Status.RUNNING,
        )
        Campaign.objects.filter(pk=campaign.pk).update(last_heartbeat_at=timezone.now())

        output = self.call()

        self.assertIn('No abandoned campaigns', output)

    def test_honours_a_custom_timeout(self):
        campaign = Campaign.objects.create(
            owner=self.user, name='c', message_text='hi', send_mode='delay',
            status=Campaign.Status.RUNNING,
        )
        Campaign.objects.filter(pk=campaign.pk).update(
            last_heartbeat_at=timezone.now() - timedelta(seconds=120)
        )

        self.call(**{'dry_run': True, 'timeout': 600})
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.RUNNING)

        self.call(**{'timeout': 60})
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.FAILED)


class ReapStaleCampaignsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw')
        self.other = User.objects.create_user('bob', password='pw')
        UserProfile.objects.create(user=self.user, whatsapp_number='+2348161234765')

    def make_campaign(self, owner=None, status=Campaign.Status.RUNNING, age_seconds=0):
        owner = owner or self.user
        campaign = Campaign.objects.create(
            owner=owner, name='c', message_text='hi', send_mode='delay', status=status
        )
        stamp = timezone.now() - timedelta(seconds=age_seconds)
        Campaign.objects.filter(pk=campaign.pk).update(
            send_started_at=stamp, last_heartbeat_at=stamp
        )
        campaign.refresh_from_db()
        return campaign

    def test_marks_abandoned_campaign_failed(self):
        campaign = self.make_campaign(age_seconds=STALE + 120)

        reaped = reap_stale_campaigns()

        self.assertEqual(reaped, [campaign.pk])
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.FAILED)
        self.assertIn('unexpectedly', campaign.status_detail)

    def test_leaves_live_campaigns_alone(self):
        campaign = self.make_campaign(age_seconds=5)
        reap_stale_campaigns()
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.RUNNING)

    def test_settles_pending_contacts_as_failed(self):
        """
        Without this the campaign shows a full progress bar of contacts stuck
        on 'pending' after a crash.
        """
        campaign = self.make_campaign(age_seconds=STALE + 120)
        pending = Contact.objects.create(
            campaign=campaign, name='A', phone='+2348161234765', status='pending'
        )
        sent = Contact.objects.create(
            campaign=campaign, name='B', phone='+2348161234766', status='sent'
        )

        reap_stale_campaigns()

        pending.refresh_from_db()
        sent.refresh_from_db()
        self.assertEqual(pending.status, 'failed')
        self.assertEqual(sent.status, 'sent', 'already-delivered contacts must not change')

    def test_can_be_scoped_to_one_owner(self):
        alice_stale = self.make_campaign(self.user, age_seconds=STALE + 120)
        bob_stale = self.make_campaign(self.other, age_seconds=STALE + 120)

        reaped = reap_stale_campaigns(owner=self.user)

        self.assertEqual(reaped, [alice_stale.pk])
        bob_stale.refresh_from_db()
        self.assertEqual(bob_stale.status, Campaign.Status.RUNNING)

    def test_stamps_a_finish_time(self):
        campaign = self.make_campaign(age_seconds=STALE + 120)
        reap_stale_campaigns()
        campaign.refresh_from_db()
        self.assertIsNotNone(campaign.send_finished_at)

    def test_returns_empty_when_nothing_is_stale(self):
        self.make_campaign(age_seconds=10)
        self.assertEqual(reap_stale_campaigns(), [])

    def test_is_idempotent(self):
        self.make_campaign(age_seconds=STALE + 120)
        first = reap_stale_campaigns()
        second = reap_stale_campaigns()
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    def test_honours_a_custom_timeout(self):
        campaign = self.make_campaign(age_seconds=120)

        self.assertEqual(reap_stale_campaigns(timeout=600), [])
        self.assertEqual(len(reap_stale_campaigns(timeout=60)), 1)

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.FAILED)
