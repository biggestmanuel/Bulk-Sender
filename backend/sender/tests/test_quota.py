"""
Tests for DailySendQuota.

The quota is the only place in the codebase with a genuine write race, so it
gets tested at that level: concurrent reservations must not both believe they
fit under the limit.

The original guard was MAX_CONTACTS_PER_CAMPAIGN (5,000 per request), which
did nothing to stop one account creating campaign after campaign all day.
"""

import threading
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from sender.models import Campaign, Contact, DailySendQuota, UserProfile

LIMIT = 100


class QuotaAccountingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw')

    def test_first_reservation_succeeds(self):
        self.assertEqual(DailySendQuota.reserve(self.user, 10, LIMIT), 10)

    def test_reservations_accumulate(self):
        DailySendQuota.reserve(self.user, 10, LIMIT)
        DailySendQuota.reserve(self.user, 15, LIMIT)
        self.assertEqual(DailySendQuota.reserve(self.user, 5, LIMIT), 30)

    def test_exactly_at_the_limit_is_allowed(self):
        """Off-by-one guard: reaching the limit should not fail."""
        self.assertEqual(DailySendQuota.reserve(self.user, LIMIT, LIMIT), LIMIT)

    def test_over_the_limit_is_rejected(self):
        DailySendQuota.reserve(self.user, LIMIT, LIMIT)
        self.assertIsNone(DailySendQuota.reserve(self.user, 1, LIMIT))

    def test_rejected_reservation_does_not_consume_allowance(self):
        """
        A rejected attempt must roll back. Otherwise repeated failures would
        consume a day's allowance with nothing sent.
        """
        DailySendQuota.reserve(self.user, 90, LIMIT)
        self.assertIsNone(DailySendQuota.reserve(self.user, 50, LIMIT))

        self.assertEqual(DailySendQuota.objects.get(user=self.user).contacts_used, 90)

        # The user can still use the remaining 10.
        self.assertEqual(DailySendQuota.reserve(self.user, 10, LIMIT), 100)
        self.assertIsNone(DailySendQuota.reserve(self.user, 1, LIMIT))

    def test_zero_reservation_is_permitted(self):
        self.assertEqual(DailySendQuota.reserve(self.user, 0, LIMIT), 0)

    def test_quota_is_per_user(self):
        other = User.objects.create_user('bob', password='pw')
        DailySendQuota.reserve(self.user, 100, LIMIT)
        self.assertEqual(DailySendQuota.reserve(other, 10, LIMIT), 10)

    def test_rolls_over_to_a_new_day(self):
        quota = DailySendQuota.objects.create(
            user=self.user,
            date=timezone.localdate() - timedelta(days=1),
            contacts_used=100,
        )
        DailySendQuota.reserve(self.user, 5, LIMIT)

        quota.refresh_from_db()
        self.assertEqual(quota.contacts_used, 5)
        self.assertEqual(quota.date, timezone.localdate())

    def test_todays_exhausted_quota_still_blocks(self):
        """Complement to the rollover test: same-day exhaustion is not reset."""
        DailySendQuota.objects.create(
            user=self.user, date=timezone.localdate(), contacts_used=100
        )
        self.assertIsNone(DailySendQuota.reserve(self.user, 5, LIMIT))
        self.assertEqual(
            DailySendQuota.objects.get(user=self.user).contacts_used, 100
        )

    def test_yesterdays_row_is_reset_rather_than_accumulating(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        quota = DailySendQuota.objects.create(
            user=self.user, date=yesterday, contacts_used=999
        )

        DailySendQuota.reserve(self.user, 5, LIMIT)

        quota.refresh_from_db()
        self.assertEqual(quota.date, timezone.localdate())
        self.assertEqual(quota.contacts_used, 5)

    def test_stale_row_from_a_week_ago_is_reset(self):
        old = timezone.localdate() - timedelta(days=7)
        DailySendQuota.objects.create(user=self.user, date=old, contacts_used=999)
        self.assertEqual(DailySendQuota.reserve(self.user, 1, LIMIT), 1)

    def test_one_row_per_user(self):
        DailySendQuota.reserve(self.user, 1, LIMIT)
        DailySendQuota.reserve(self.user, 1, LIMIT)
        self.assertEqual(DailySendQuota.objects.filter(user=self.user).count(), 1)

    def test_str(self):
        DailySendQuota.reserve(self.user, 5, LIMIT)
        quota = DailySendQuota.objects.get(user=self.user)
        self.assertIn('alice', str(quota))
        self.assertIn('5', str(quota))


class ConcurrentReservationTests(TransactionTestCase):
    """
    The reason reserve() uses a conditional UPDATE rather than read-then-write.

    A plain "read current, check under limit, write" would let two threads
    both read 0, both see room for 50, and both write 50 — allowing 100
    against a limit of 50.

    IMPORTANT — these tests skip on in-memory SQLite. Django's test runner
    creates the test database as a shared in-memory SQLite, which serialises
    writes with a table lock rather than a row lock, so concurrent threads
    fail with "database table is locked" before the logic under test is ever
    reached. On a file-backed or Postgres database they run for real.

    That constraint is not hypothetical: the production app runs background
    send threads against SQLite, so the busy timeout in settings.py
    (SQLITE_TIMEOUT_SECONDS, default 20s) is what keeps those threads from
    failing outright. See the README's known limitations.
    """

    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw')

    def require_real_concurrency(self):
        if connection.vendor != 'sqlite':
            return
        name = connection.settings_dict.get('NAME') or ''
        if ':memory:' in str(name) or 'file:memorydb' in str(name):
            self.skipTest(
                'in-memory SQLite serialises writes with a table lock; '
                'concurrent reservation cannot be exercised here. Set '
                'DATABASE_PATH to a file-backed database to run these.'
            )

    def reserve_from_threads(self, amount, limit, threads=8):
        results = []
        errors = []
        barrier = threading.Barrier(threads)

        def worker():
            try:
                barrier.wait(timeout=10)
                results.append(DailySendQuota.reserve(self.user, amount, limit))
            except Exception as exc:  # pragma: no cover - diagnostic only
                errors.append(exc)

        workers = [threading.Thread(target=worker) for _ in range(threads)]
        for w in workers:
            w.start()
        for w in workers:
            w.join(timeout=30)

        self.assertEqual(errors, [], f'reserve() raised: {errors}')
        return results

    def test_concurrent_reservations_cannot_exceed_the_limit(self):
        """8 threads each reserving 50 against a limit of 100 -> at most 2."""
        self.require_real_concurrency()
        results = self.reserve_from_threads(amount=50, limit=100)

        granted = [r for r in results if r is not None]
        self.assertLessEqual(len(granted), 2, f'too many reservations granted: {results}')

        quota = DailySendQuota.objects.get(user=self.user)
        self.assertLessEqual(quota.contacts_used, 100)

    def test_only_one_thread_wins_for_a_single_slot(self):
        self.require_real_concurrency()
        results = self.reserve_from_threads(amount=100, limit=100)
        self.assertEqual(len([r for r in results if r is not None]), 1)

    def test_counter_never_exceeds_limit_under_load(self):
        self.require_real_concurrency()
        results = self.reserve_from_threads(amount=10, limit=50)

        quota = DailySendQuota.objects.get(user=self.user)
        self.assertLessEqual(quota.contacts_used, 50)
        self.assertEqual(
            quota.contacts_used, len([r for r in results if r is not None]) * 10
        )

    def test_a_rejected_thread_leaves_no_trace(self):
        self.require_real_concurrency()
        self.reserve_from_threads(amount=50, limit=100)
        quota = DailySendQuota.objects.get(user=self.user)
        self.assertLessEqual(quota.contacts_used, 100)


class QuotaIntegrationTests(TestCase):
    """The quota as enforced through start_sending, not just the helper."""

    def setUp(self):
        from django.core.cache import cache

        from sender import views

        cache.clear()
        views._active_sends.clear()

        self.user = User.objects.create_user('alice', password='pw')
        UserProfile.objects.create(user=self.user, whatsapp_number='+2348161234765')

        from rest_framework.authtoken.models import Token
        from rest_framework.test import APIClient

        self.token = Token.objects.create(user=self.user).key
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token}')

    def make_campaign(self, count=5):
        campaign = Campaign.objects.create(
            owner=self.user, name='c', message_text='hi', send_mode='delay'
        )
        Contact.objects.bulk_create([
            Contact(campaign=campaign, name=f'C{i}', phone=f'+23481612347{i:02d}')
            for i in range(count)
        ])
        return campaign

    def start(self, campaign):
        from unittest import mock

        from sender import views

        with mock.patch.object(views.threading, 'Thread'):
            return self.client.post(f'/api/campaigns/{campaign.pk}/start/')

    def test_quota_blocks_a_send_that_would_exceed_it(self):
        """
        First campaign fits (60 of 100), second pushes past the limit (110).
        This is the case the original per-request cap could not catch: each
        request was individually under 5,000.
        """
        from django.test import override_settings

        rates = self._rates()
        campaign = self.make_campaign(count=60)

        with override_settings(REST_FRAMEWORK={
            **rates,
            'DEFAULT_THROTTLE_RATES': {
                **rates['DEFAULT_THROTTLE_RATES'], 'send_quota': '100/day',
            },
        }):
            first = self.start(campaign)
            self.assertEqual(first.status_code, 202)

            Campaign.objects.filter(pk=campaign.pk).update(status=Campaign.Status.COMPLETED)

            second_campaign = self.make_campaign(count=50)
            second = self.start(second_campaign)

        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.data['code'], 'quota_exceeded')

    def test_first_send_that_already_exceeds_the_limit_is_rejected(self):
        """A single oversized campaign is refused too, not just cumulative ones."""
        from django.test import override_settings

        rates = self._rates()
        campaign = self.make_campaign(count=60)

        with override_settings(REST_FRAMEWORK={
            **rates,
            'DEFAULT_THROTTLE_RATES': {
                **rates['DEFAULT_THROTTLE_RATES'], 'send_quota': '10/day',
            },
        }):
            response = self.start(campaign)

        self.assertEqual(response.status_code, 429)
        # A zero-valued row for today is left behind, which is harmless and
        # arguably useful. What must not happen is the reservation being
        # consumed: nothing was sent, so nothing is counted.
        quota = DailySendQuota.objects.get(user=self.user)
        self.assertEqual(quota.contacts_used, 0)
        self.assertEqual(quota.date, timezone.localdate())

    def test_quota_message_names_the_limit(self):
        from django.test import override_settings

        campaign = self.make_campaign(count=10)
        DailySendQuota.reserve(self.user, 100, 100)

        with override_settings(REST_FRAMEWORK={
            **self._rates(), 'DEFAULT_THROTTLE_RATES': {
                **self._rates()['DEFAULT_THROTTLE_RATES'], 'send_quota': '100/day',
            }
        }):
            response = self.start(campaign)

        self.assertEqual(response.status_code, 429)
        self.assertIn('100', response.data['error'])

    @staticmethod
    def _rates():
        from django.conf import settings as django_settings

        return dict(django_settings.REST_FRAMEWORK)


class QuotaCleanupTests(TestCase):
    def test_deleting_a_user_removes_their_quota(self):
        user = User.objects.create_user('alice', password='pw')
        DailySendQuota.reserve(user, 5, 100)
        self.assertEqual(DailySendQuota.objects.filter(user=user).count(), 1)

        user.delete()
        self.assertEqual(DailySendQuota.objects.count(), 0)

    def test_contact_deletion_leaves_the_quota_intact(self):
        """Deleting a campaign must not hand the allowance back."""
        user = User.objects.create_user('alice', password='pw')
        campaign = Campaign.objects.create(owner=user, name='c', message_text='hi')
        Contact.objects.create(campaign=campaign, name='a', phone='+2348161234765')

        DailySendQuota.reserve(user, 5, 100)
        campaign.delete()

        self.assertEqual(DailySendQuota.objects.get(user=user).contacts_used, 5)
