"""
Tests for campaign listing, retry, and delete.

These three capabilities were missing entirely, so the tests focus on the
cases that are easy to get wrong: ownership isolation, the interaction with
in-flight campaigns, and not touching contacts that were already delivered.
"""

import json
import os
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from sender.models import Campaign, Contact, MediaFile, UserProfile


class APITestCase(TestCase):
    def setUp(self):
        from django.core.cache import cache

        from sender import views

        cache.clear()
        views._active_sends.clear()

        self.client = APIClient()

    def make_user(self, username, whatsapp_number='+2348161234765'):
        user = User.objects.create_user(username, password='pw')
        Token.objects.create(user=user)
        UserProfile.objects.create(user=user, whatsapp_number=whatsapp_number)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.get(user=user).key}')
        return user

    def make_campaign(self, owner, statuses=(), name='Campaign', **extra):
        campaign = Campaign.objects.create(
            owner=owner, name=name, message_text='hi', send_mode='delay', **extra
        )
        for index, status in enumerate(statuses):
            Contact.objects.create(
                campaign=campaign,
                name=f'C{index}',
                phone=f'+23481612347{index:02d}',
                status=status,
            )
        return campaign

    def patch_send_thread(self):
        from unittest import mock

        from sender import views

        return mock.patch.object(views.threading, 'Thread')


# --- Listing -------------------------------------------------------------


class ListCampaignsTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user('alice')

    def test_empty_list(self):
        response = self.client.get('/api/campaigns/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['campaigns'], [])
        self.assertEqual(response.data['total'], 0)

    def test_orphaned_campaign_is_listed_as_failed_not_sending(self):
        """
        The list is where a user would notice a send that never recovered, so
        it has to agree with reality rather than showing "sending" forever.
        """
        campaign = self.make_campaign(
            self.user, statuses=['pending'], status=Campaign.Status.RUNNING
        )
        Campaign.objects.filter(pk=campaign.pk).update(
            last_heartbeat_at=timezone.now() - timedelta(seconds=60 * 60)
        )

        response = self.client.get('/api/campaigns/')

        row = next(c for c in response.data['campaigns'] if c['id'] == campaign.pk)
        self.assertEqual(row['status'], 'failed')
        self.assertTrue(row['can_retry'], 'the unsent contacts should be retryable')

    def test_a_live_send_is_still_shown_as_sending(self):
        campaign = self.make_campaign(
            self.user, statuses=['pending'], status=Campaign.Status.RUNNING
        )
        Campaign.objects.filter(pk=campaign.pk).update(last_heartbeat_at=timezone.now())

        response = self.client.get('/api/campaigns/')

        row = next(c for c in response.data['campaigns'] if c['id'] == campaign.pk)
        self.assertEqual(row['status'], 'running')

    def test_lists_campaigns_newest_first(self):
        """
        Ordering is asserted by explicitly distinct timestamps: created_at is
        auto_now_add, so two campaigns created in the same millisecond would
        otherwise order arbitrarily and this test would be flaky.
        """
        first = self.make_campaign(self.user, name='First')
        second = self.make_campaign(self.user, name='Second')

        base = timezone.now()
        Campaign.objects.filter(pk=first.pk).update(created_at=base - timedelta(hours=2))
        Campaign.objects.filter(pk=second.pk).update(created_at=base - timedelta(hours=1))

        response = self.client.get('/api/campaigns/')

        names = [c['name'] for c in response.data['campaigns']]
        self.assertEqual(names, ['Second', 'First'])

    def test_includes_progress_counts(self):
        self.make_campaign(
            self.user,
            statuses=['sent', 'sent', 'failed', 'pending'],
            status=Campaign.Status.RUNNING,
        )

        campaign = self.client.get('/api/campaigns/').data['campaigns'][0]

        self.assertEqual(campaign['total_contacts'], 4)
        self.assertEqual(campaign['sent_contacts'], 2)
        self.assertEqual(campaign['failed_contacts'], 1)
        self.assertEqual(campaign['pending_contacts'], 1)

    def test_omits_contacts_and_message_body(self):
        """
        The list must stay cheap: returning every contact would decrypt the
        whole table for a summary view.
        """
        self.make_campaign(self.user, statuses=['sent', 'failed'])

        campaign = self.client.get('/api/campaigns/').data['campaigns'][0]

        self.assertNotIn('contacts', campaign)
        self.assertNotIn('message_text', campaign)

    def test_never_shows_another_users_campaigns(self):
        self.make_campaign(self.user, name='Mine')
        other = User.objects.create_user('bob', password='pw')
        self.make_campaign(other, name='Theirs')

        response = self.client.get('/api/campaigns/')

        names = [c['name'] for c in response.data['campaigns']]
        self.assertEqual(names, ['Mine'])

    def test_requires_authentication(self):
        self.client.credentials()
        self.assertIn(self.client.get('/api/campaigns/').status_code, (401, 403))

    def test_paginates(self):
        for i in range(5):
            self.make_campaign(self.user, name=f'C{i}')

        response = self.client.get('/api/campaigns/?page_size=2')

        self.assertEqual(len(response.data['campaigns']), 2)
        self.assertEqual(response.data['total'], 5)
        self.assertTrue(response.data['has_next'])

        page2 = self.client.get('/api/campaigns/?page_size=2&page=2')
        self.assertNotEqual(
            [c['id'] for c in page2.data['campaigns']],
            [c['id'] for c in response.data['campaigns']],
        )

    def test_last_page_reports_no_next(self):
        self.make_campaign(self.user, name='Only')
        response = self.client.get('/api/campaigns/?page_size=10')
        self.assertFalse(response.data['has_next'])

    def test_page_size_is_capped(self):
        response = self.client.get('/api/campaigns/?page_size=100000')
        self.assertLessEqual(response.data['page_size'], 100)

    def test_ignores_nonsense_pagination_params(self):
        response = self.client.get('/api/campaigns/?page=abc&page_size=-5')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['page'], 1)
        self.assertGreater(response.data['page_size'], 0)

    def test_reports_whether_retry_is_possible(self):
        self.make_campaign(self.user, statuses=['failed'], status=Campaign.Status.FAILED)
        self.make_campaign(self.user, statuses=['sent'], name='AllSent', status=Campaign.Status.COMPLETED)

        campaigns = {c['name']: c for c in self.client.get('/api/campaigns/').data['campaigns']}

        self.assertTrue(campaigns['Campaign']['can_retry'])
        self.assertFalse(campaigns['AllSent']['can_retry'])

    def test_media_count_is_included(self):
        campaign = self.make_campaign(self.user)
        from sender.models import MediaFile

        MediaFile.objects.create(
            campaign=campaign,
            file=SimpleUploadedFile('pic.png', b'x', content_type='image/png'),
        )

        listed = self.client.get('/api/campaigns/').data['campaigns'][0]
        self.assertEqual(listed['media_count'], 1)

    def test_summary_view_does_not_load_contacts(self):
        """
        Guards the specific regression that mattered: returning contacts from
        the summary would decrypt every contact row for every campaign. The
        list annotates counts and prefetches media instead, so no query may
        select from sender_contact for its content.
        """
        for i in range(5):
            self.make_campaign(self.user, statuses=['sent', 'failed'], name=f'C{i}')

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get('/api/campaigns/?page_size=100')

        self.assertEqual(len(response.data['campaigns']), 5)

        # The single aggregate query joins contacts to count them, which is
        # fine. What must not happen is a query that SELECTs contact columns.
        contact_selects = [
            q['sql'] for q in captured.captured_queries
            if 'FROM "sender_contact"' in q['sql'] and 'COUNT' not in q['sql'].upper()
        ]
        self.assertEqual(
            contact_selects, [],
            'the summary must not fetch individual contact rows',
        )


# --- Retry ---------------------------------------------------------------


class RetryTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user('alice')

    def test_resets_failed_contacts_to_pending(self):
        campaign = self.make_campaign(
            self.user, statuses=['sent', 'failed', 'failed'], status=Campaign.Status.FAILED
        )

        response = self.client.post(f'/api/campaigns/{campaign.pk}/retry/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['reset_count'], 2)

        statuses = sorted(campaign.contacts.values_list('status', flat=True))
        self.assertEqual(statuses, ['pending', 'pending', 'sent'])

    def test_does_not_touch_already_sent_contacts(self):
        """
        Retrying must not re-send to people who already received the message.
        This is the single most damaging thing this endpoint could get wrong.
        """
        campaign = self.make_campaign(self.user, statuses=['sent'], status=Campaign.Status.COMPLETED)

        self.client.post(f'/api/campaigns/{campaign.pk}/retry/')

        self.assertEqual(
            list(campaign.contacts.values_list('status', flat=True)), ['sent']
        )

    def test_resets_campaign_status_to_draft(self):
        campaign = self.make_campaign(self.user, statuses=['failed'], status=Campaign.Status.FAILED)

        self.client.post(f'/api/campaigns/{campaign.pk}/retry/')

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.DRAFT)
        self.assertEqual(campaign.status_detail, '')

    def test_clears_timestamps(self):
        campaign = self.make_campaign(self.user, statuses=['failed'], status=Campaign.Status.FAILED)
        Campaign.objects.filter(pk=campaign.pk).update(send_finished_at=timezone.now())

        self.client.post(f'/api/campaigns/{campaign.pk}/retry/')

        campaign.refresh_from_db()
        self.assertIsNone(campaign.send_finished_at)
        self.assertIsNone(campaign.send_started_at)

    def test_nothing_to_retry_is_not_an_error(self):
        campaign = self.make_campaign(self.user, statuses=['sent'], status=Campaign.Status.COMPLETED)

        response = self.client.post(f'/api/campaigns/{campaign.pk}/retry/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['reset_count'], 0)

    def test_refuses_while_campaign_is_sending(self):
        campaign = self.make_campaign(self.user, statuses=['failed'], status=Campaign.Status.RUNNING)

        response = self.client.post(f'/api/campaigns/{campaign.pk}/retry/')

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'already_sending')
        # Must not have reset anything.
        self.assertEqual(
            list(campaign.contacts.values_list('status', flat=True)), ['failed']
        )

    def test_never_retries_another_users_campaign(self):
        other = User.objects.create_user('bob', password='pw')
        campaign = self.make_campaign(other, statuses=['failed'], status=Campaign.Status.FAILED)

        response = self.client.post(f'/api/campaigns/{campaign.pk}/retry/')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            list(campaign.contacts.values_list('status', flat=True)), ['failed']
        )

    def test_reaps_a_stale_campaign_before_retrying(self):
        """
        If the worker died, retry must settle it first — otherwise the live
        worker and this request race over the same contact rows.
        """
        campaign = self.make_campaign(self.user, statuses=['pending'], status=Campaign.Status.RUNNING)
        Campaign.objects.filter(pk=campaign.pk).update(
            last_heartbeat_at=timezone.now() - timedelta(seconds=60 * 60)
        )

        response = self.client.post(f'/api/campaigns/{campaign.pk}/retry/')

        self.assertEqual(response.status_code, 200)

    def test_does_not_start_the_send_itself(self):
        """
        Retry only resets rows. Restarting is a separate call so the existing
        guards still apply and the outcome is observable.
        """
        campaign = self.make_campaign(self.user, statuses=['failed'], status=Campaign.Status.FAILED)

        with self.patch_send_thread() as thread:
            self.client.post(f'/api/campaigns/{campaign.pk}/retry/')

        thread.assert_not_called()
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.DRAFT)

    def test_can_start_after_retrying(self):
        campaign = self.make_campaign(self.user, statuses=['failed', 'failed'], status=Campaign.Status.FAILED)

        self.client.post(f'/api/campaigns/{campaign.pk}/retry/')

        with self.patch_send_thread():
            response = self.client.post(f'/api/campaigns/{campaign.pk}/start/')

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['pending'], 2)


# --- Delete --------------------------------------------------------------


class DeleteTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user('alice')

    def test_deletes_campaign_and_contacts(self):
        campaign = self.make_campaign(self.user, statuses=['sent', 'failed'])

        response = self.client.delete(f'/api/campaigns/{campaign.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Campaign.objects.filter(pk=campaign.pk).exists())
        self.assertEqual(Contact.objects.filter(campaign_id=campaign.pk).count(), 0)

    def test_never_deletes_another_users_campaign(self):
        other = User.objects.create_user('bob', password='pw')
        campaign = self.make_campaign(other, statuses=['sent'])

        response = self.client.delete(f'/api/campaigns/{campaign.pk}/')

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Campaign.objects.filter(pk=campaign.pk).exists())

    def test_refuses_while_sending(self):
        campaign = self.make_campaign(self.user, statuses=['pending'], status=Campaign.Status.RUNNING)

        response = self.client.delete(f'/api/campaigns/{campaign.pk}/')

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'already_sending')
        self.assertTrue(Campaign.objects.filter(pk=campaign.pk).exists())

    def test_missing_campaign_is_404(self):
        response = self.client.delete('/api/campaigns/999999/')
        self.assertEqual(response.status_code, 404)

    def test_orphaned_campaign_can_be_deleted(self):
        """
        A campaign whose worker died stays 'running' until something triggers
        recovery. Without reaping on the delete path, every attempt returned
        409 forever — the same permanent lockout that retry_campaign already
        guards against.
        """
        campaign = self.make_campaign(
            self.user, statuses=['pending'], status=Campaign.Status.RUNNING
        )
        Campaign.objects.filter(pk=campaign.pk).update(
            last_heartbeat_at=timezone.now() - timedelta(seconds=60 * 60)
        )

        response = self.client.delete(f'/api/campaigns/{campaign.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Campaign.objects.filter(pk=campaign.pk).exists())

    def test_a_live_campaign_is_still_protected(self):
        campaign = self.make_campaign(
            self.user, statuses=['pending'], status=Campaign.Status.RUNNING
        )
        Campaign.objects.filter(pk=campaign.pk).update(last_heartbeat_at=timezone.now())

        response = self.client.delete(f'/api/campaigns/{campaign.pk}/')

        self.assertEqual(response.status_code, 409)

    def test_media_files_are_removed_from_storage(self):
        """
        Deleting the row without deleting the file would orphan it in
        MEDIA_ROOT forever — nothing else ever references it.
        """
        campaign = self.make_campaign(self.user, statuses=['sent'])
        media = MediaFile.objects.create(
            campaign=campaign,
            file=SimpleUploadedFile('pic.png', b'x' * 32, content_type='image/png'),
        )
        path = media.file.path
        self.assertTrue(os.path.exists(path))

        response = self.client.delete(f'/api/campaigns/{campaign.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.exists(path), 'the attachment should be gone from disk')

    def test_deleting_a_campaign_leaves_other_campaigns_media_alone(self):
        keep = self.make_campaign(self.user, name='keep', statuses=['sent'])
        keep_media = MediaFile.objects.create(
            campaign=keep,
            file=SimpleUploadedFile('keep.png', b'k' * 16, content_type='image/png'),
        )
        keep_path = keep_media.file.path

        drop = self.make_campaign(self.user, name='drop', statuses=['sent'])
        MediaFile.objects.create(
            campaign=drop,
            file=SimpleUploadedFile('drop.png', b'd' * 16, content_type='image/png'),
        )

        self.client.delete(f'/api/campaigns/{drop.pk}/')

        self.assertTrue(os.path.exists(keep_path), 'unrelated media must survive')
        self.assertTrue(MediaFile.objects.filter(pk=keep_media.pk).exists())

    def test_requires_authentication(self):
        campaign = self.make_campaign(self.user, statuses=['sent'])
        self.client.credentials()
        self.assertIn(
            self.client.delete(f'/api/campaigns/{campaign.pk}/').status_code, (401, 403)
        )