"""
Tests for run_campaign_send — the background worker that ties the database
to the automation module.

send_whatsapp_messages is mocked throughout: what matters here is that the
worker's bookkeeping is right, since that is what the progress UI and the
quota depend on.
"""

from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from sender.models import Campaign, Contact, UserProfile, WhatsAppLoginState
from sender.views import run_campaign_send


def sent_result(sent=1, failed=0, logged_in=True):
    return {'sent': sent, 'failed': failed, 'logged_in': logged_in}


class WorkerTestCase(TestCase):
    def setUp(self):
        from django.core.cache import cache

        from sender import views

        cache.clear()
        views._active_sends.clear()

        self.user = User.objects.create_user('alice', password='pw')
        UserProfile.objects.create(user=self.user, whatsapp_number='+2348161234765')
        self.campaign = Campaign.objects.create(
            owner=self.user, name='c', message_text='Hi {name}', send_mode='delay'
        )
        self.contacts = [
            Contact.objects.create(
                campaign=self.campaign, name=f'C{i}', phone=f'+23481612347{i:02d}'
            )
            for i in range(3)
        ]

    def run_worker(self, result=None, **send_kwargs):
        result = result if result is not None else sent_result()
        with mock.patch(
            'sender.views.send_whatsapp_messages', return_value=result
        ) as send:
            run_campaign_send(self.campaign.pk, self.user.pk)
        return send


class HappyPathTests(WorkerTestCase):
    def test_campaign_is_marked_completed(self):
        self.run_worker()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.COMPLETED)
        self.assertIn('Sent 1 message', self.campaign.status_detail)
        self.assertIsNotNone(self.campaign.send_finished_at)

    def test_timestamps_are_set(self):
        self.run_worker()
        self.campaign.refresh_from_db()
        self.assertIsNotNone(self.campaign.send_started_at)
        self.assertIsNotNone(self.campaign.send_finished_at)
        self.assertLessEqual(self.campaign.send_started_at, self.campaign.send_finished_at)

    def test_progress_callback_updates_contacts(self):
        seen = []

        def fake_send(**kwargs):
            for contact in kwargs['contacts']:
                kwargs['on_progress'](contact['id'], 'sent')
            return sent_result(sent=len(kwargs['contacts']))

        with mock.patch('sender.views.send_whatsapp_messages', side_effect=fake_send):
            run_campaign_send(self.campaign.pk, self.user.pk)

        self.assertEqual(
            sorted(Contact.objects.values_list('status', flat=True)), ['sent', 'sent', 'sent']
        )
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.progress()['sent'], 3)

    def test_deletes_stale_login_state_before_starting(self):
        """
        A code left over from a previous campaign would otherwise be shown to
        the user as if it belonged to this send.
        """
        WhatsAppLoginState.objects.create(
            user=self.user,
            phase=WhatsAppLoginState.Phase.WAITING_FOR_CODE,
            login_code='STALE12',
        )
        self.run_worker()
        state = WhatsAppLoginState.objects.get(user=self.user)
        self.assertNotEqual(state.login_code, 'STALE12')
        self.assertEqual(state.phase, WhatsAppLoginState.Phase.LOGGED_IN)

    def test_only_pending_contacts_are_sent(self):
        Contact.objects.filter(pk=self.contacts[0].pk).update(status='sent')
        send = self.run_worker()
        sent_ids = [c['id'] for c in send.call_args.kwargs['contacts']]
        self.assertNotIn(self.contacts[0].pk, sent_ids)
        self.assertEqual(len(sent_ids), 2)

    def test_login_code_callback_persists_to_database(self):
        """The code the automation generates must be readable by the UI poll."""
        def fake_send(**kwargs):
            kwargs['on_login_code']('ABCD1234')
            return sent_result(sent=0, failed=0, logged_in=False)

        with mock.patch('sender.views.send_whatsapp_messages', side_effect=fake_send):
            run_campaign_send(self.campaign.pk, self.user.pk)

        state = WhatsAppLoginState.objects.get(user=self.user)
        self.assertEqual(state.login_code, 'ABCD1234')
        self.assertEqual(state.phase, WhatsAppLoginState.Phase.WAITING_FOR_CODE)
        self.assertIsNotNone(state.code_generated_at)

    def test_login_code_error_callback_is_persisted(self):
        def fake_send(**kwargs):
            kwargs['on_login_code_error']('WhatsApp changed their login page')
            return sent_result(logged_in=False)

        with mock.patch('sender.views.send_whatsapp_messages', side_effect=fake_send):
            run_campaign_send(self.campaign.pk, self.user.pk)

        self.assertEqual(
            WhatsAppLoginState.objects.get(user=self.user).error,
            'WhatsApp changed their login page',
        )

    def test_successful_login_clears_the_code(self):
        """A code is single-use; leaving it set would keep the overlay up."""
        def fake_send(**kwargs):
            kwargs['on_login_code']('ABCD1234')
            return sent_result()

        with mock.patch('sender.views.send_whatsapp_messages', side_effect=fake_send):
            run_campaign_send(self.campaign.pk, self.user.pk)

        state = WhatsAppLoginState.objects.get(user=self.user)
        self.assertEqual(state.login_code, '')
        self.assertEqual(state.phase, WhatsAppLoginState.Phase.LOGGED_IN)

    def test_whatsapp_number_is_passed_through(self):
        send = self.run_worker()
        self.assertEqual(send.call_args.kwargs['whatsapp_number'], '+2348161234765')

    def test_slow_network_flag_persists(self):
        captured = {}

        def fake_send(**kwargs):
            captured['on_login_slow'] = kwargs['on_login_slow']
            return sent_result()

        with mock.patch('sender.views.send_whatsapp_messages', side_effect=fake_send):
            run_campaign_send(self.campaign.pk, self.user.pk)
            captured['on_login_slow']()
            self.assertEqual(
                WhatsAppLoginState.objects.get(user=self.user).phase,
                WhatsAppLoginState.Phase.SLOW,
            )


class FailurePathTests(WorkerTestCase):
    def test_login_failure_marks_campaign_failed(self):
        self.run_worker(result=sent_result(sent=0, failed=3, logged_in=False))
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.FAILED)
        self.assertIn('login', self.campaign.status_detail.lower())

    def test_login_failure_settles_every_contact(self):
        """Otherwise the progress bar spins forever."""
        self.run_worker(result=sent_result(sent=0, failed=3, logged_in=False))
        self.assertEqual(
            sorted(set(Contact.objects.values_list('status', flat=True))), ['failed']
        )

    def test_unexpected_exception_is_contained(self):
        with mock.patch(
            'sender.views.send_whatsapp_messages', side_effect=RuntimeError('chromium exploded')
        ):
            run_campaign_send(self.campaign.pk, self.user.pk)

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.FAILED)
        self.assertIn('internal error', self.campaign.status_detail)
        self.assertEqual(
            sorted(set(Contact.objects.values_list('status', flat=True))), ['failed']
        )

    def test_missing_campaign_is_a_no_op(self):
        """No exception, because the thread is fire-and-forget."""
        run_campaign_send(999999, self.user.pk)

    def test_missing_phone_number_fails_the_campaign(self):
        UserProfile.objects.filter(user=self.user).update(whatsapp_number='')
        self.run_worker()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.FAILED)
        self.assertIn('no whatsapp number is set', self.campaign.status_detail.lower())

    def test_campaign_with_nothing_pending_completes(self):
        Contact.objects.filter(campaign=self.campaign).update(status='sent')
        self.run_worker()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.COMPLETED)
        self.assertIn('No pending contacts', self.campaign.status_detail)

    def test_in_flight_registry_is_cleared_afterwards(self):
        from sender import views

        views._active_sends.add((self.campaign.pk, self.user.pk))
        self.run_worker()
        self.assertNotIn((self.campaign.pk, self.user.pk), views._active_sends)

    def test_in_flight_registry_cleared_even_when_send_raises(self):
        from sender import views

        views._active_sends.add((self.campaign.pk, self.user.pk))
        with mock.patch('sender.views.send_whatsapp_messages', side_effect=RuntimeError('x')):
            run_campaign_send(self.campaign.pk, self.user.pk)
        self.assertNotIn((self.campaign.pk, self.user.pk), views._active_sends)

    def test_another_users_campaign_is_untouched(self):
        other = User.objects.create_user('bob', password='pw')
        other_campaign = Campaign.objects.create(
            owner=other, name='theirs', message_text='hi', send_mode='delay'
        )
        Contact.objects.create(campaign=other_campaign, name='X', phone='+2348161234769')

        run_campaign_send(other_campaign.pk, self.user.pk)

        other_campaign.refresh_from_db()
        self.assertEqual(other_campaign.status, Campaign.Status.DRAFT)


class NoSecondDeliveryTests(TestCase):
    """
    The end-to-end guarantee, through the real database.

    The duplicate-delivery rules are each unit-tested in isolation — the retry
    guard in test_whatsapp_automation, the crash settlement, and retry_campaign
    leaving sent contacts alone in test_campaigns_api. None of them crosses the
    seams, and this project's worst outcome is a real person receiving the same
    message twice. So this drives the whole path: a send that delivers one
    contact and then dies, the worker's bookkeeping, the retry endpoint, and a
    second start.

    If any layer ever re-queues a delivered contact, this fails.
    """

    def setUp(self):
        from django.core.cache import cache

        from sender import views
        from rest_framework.authtoken.models import Token
        from rest_framework.test import APIClient

        cache.clear()
        views._active_sends.clear()

        self.user = User.objects.create_user('alice', password='pw')
        UserProfile.objects.create(user=self.user, whatsapp_number='+2348161234765')
        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=self.user).key}'
        )

        self.campaign = Campaign.objects.create(
            owner=self.user, name='c', message_text='Hi {name}', send_mode='delay'
        )
        self.contacts = [
            Contact.objects.create(
                campaign=self.campaign, name=f'C{i}', phone=f'+23481612347{i:02d}'
            )
            for i in range(3)
        ]

    def crash_after_first_delivery(self, **kwargs):
        """
        A send that delivers contact 1, fails contact 2, never reaches
        contact 3, and reports the browser gone.
        """
        def fake_send(**send_kwargs):
            progress = send_kwargs['on_progress']
            progress(self.contacts[0].pk, 'sent')
            progress(self.contacts[1].pk, 'failed')
            return {'sent': 1, 'failed': 1, 'logged_in': False}

        with mock.patch('sender.views.send_whatsapp_messages', side_effect=fake_send):
            run_campaign_send(self.campaign.pk, self.user.pk)

    def statuses(self):
        return dict(
            Contact.objects.filter(campaign=self.campaign).values_list('pk', 'status')
        )

    def test_a_crash_leaves_the_delivered_contact_sent(self):
        self.crash_after_first_delivery()

        self.assertEqual(
            self.statuses()[self.contacts[0].pk], 'sent',
            'a delivered contact must stay sent after the browser dies',
        )

    def test_a_crash_marks_the_campaign_failed_not_completed(self):
        self.crash_after_first_delivery()

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.FAILED)

    def test_nothing_is_left_pending_after_a_crash(self):
        """Otherwise the progress bar spins forever instead of finishing."""
        self.crash_after_first_delivery()

        self.assertNotIn('pending', self.statuses().values())

    def test_retry_never_requeues_a_delivered_contact(self):
        """
        The seam this test exists for. retry_campaign flips failed back to
        pending; if it ever did that to a sent contact, the next start would
        message someone who already got the message.
        """
        self.crash_after_first_delivery()

        response = self.client.post(f'/api/campaigns/{self.campaign.pk}/retry/')

        self.assertEqual(response.status_code, 200)
        statuses = self.statuses()
        self.assertEqual(
            statuses[self.contacts[0].pk], 'sent',
            'retry handed an already-delivered contact back for sending',
        )

    def test_retry_requeues_exactly_the_missed_contacts(self):
        self.crash_after_first_delivery()
        self.client.post(f'/api/campaigns/{self.campaign.pk}/retry/')

        statuses = self.statuses()
        self.assertEqual(statuses[self.contacts[1].pk], 'pending')
        self.assertEqual(statuses[self.contacts[2].pk], 'pending')

    def test_second_start_sends_only_the_missed_contacts(self):
        """
        The last seam: /start/ must hand the automation only what is pending,
        so a delivered contact is never passed to the browser again.
        """
        self.crash_after_first_delivery()
        self.client.post(f'/api/campaigns/{self.campaign.pk}/retry/')

        captured = {}

        def capture_send(**send_kwargs):
            captured['ids'] = [c['id'] for c in send_kwargs['contacts']]
            return {'sent': len(send_kwargs['contacts']), 'failed': 0, 'logged_in': True}

        with mock.patch('sender.views.send_whatsapp_messages', side_effect=capture_send):
            run_campaign_send(self.campaign.pk, self.user.pk)

        self.assertNotIn(
            self.contacts[0].pk, captured['ids'],
            'a delivered contact was passed to the browser a second time',
        )
        self.assertEqual(
            sorted(captured['ids']),
            sorted([self.contacts[1].pk, self.contacts[2].pk]),
        )


class ProgressTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw')
        self.campaign = Campaign.objects.create(
            owner=self.user, name='c', message_text='hi', send_mode='delay'
        )

    def test_counts_every_status(self):
        Contact.objects.create(campaign=self.campaign, name='a', phone='+2348161234701', status='sent')
        Contact.objects.create(campaign=self.campaign, name='b', phone='+2348161234702', status='sent')
        Contact.objects.create(campaign=self.campaign, name='c', phone='+2348161234703', status='failed')
        Contact.objects.create(campaign=self.campaign, name='d', phone='+2348161234704')

        self.assertEqual(
            self.campaign.progress(),
            {'total': 4, 'sent': 2, 'failed': 1, 'pending': 1},
        )

    def test_empty_campaign(self):
        self.assertEqual(
            self.campaign.progress(),
            {'total': 0, 'sent': 0, 'failed': 0, 'pending': 0},
        )

    def test_progress_uses_a_single_query(self):
        """
        The status endpoint is polled every 3 seconds per user. With 5,000
        contacts, decrypting every row per poll was the difference between a
        few KB and several MB of work every three seconds.
        """
        Contact.objects.bulk_create([
            Contact(campaign=self.campaign, name=f'c{i}', phone=f'+23481612347{i:02d}')
            for i in range(50)
        ])
        with self.assertNumQueries(1):
            self.campaign.progress()