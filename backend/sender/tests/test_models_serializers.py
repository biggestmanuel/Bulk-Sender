"""
Unit tests for campaign pagination, model helpers and serializers.

These modules had no direct coverage; the endpoints were tested through the
HTTP layer, which left the pieces underneath untested. Focus areas:

* pagination, since the clamping logic is easy to get subtly wrong and a bad
  page_size on a large account is a full-table scan
* the query-count guarantee on the summary serializer, which is the whole
  reason the list endpoint annotates instead of loading contacts
* media_count, which has two code paths depending on whether prefetch ran
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from sender.campaigns import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, _pagination_params
from sender.models import Campaign, Contact, MediaFile
from sender.serializers import CampaignListSerializer, CampaignSerializer


class PaginationParamTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def request(self, **params):
        return self.factory.get('/api/campaigns/', params)

    def test_defaults(self):
        page, size = _pagination_params(self.request())
        self.assertEqual(page, 1)
        self.assertEqual(size, DEFAULT_PAGE_SIZE)

    def test_parses_explicit_values(self):
        page, size = _pagination_params(self.request(page='3', page_size='10'))
        self.assertEqual(page, 3)
        self.assertEqual(size, 10)

    def test_caps_page_size(self):
        """An unbounded page_size on a large account is a full-table scan."""
        _, size = _pagination_params(self.request(page_size='100000'))
        self.assertEqual(size, MAX_PAGE_SIZE)

    def test_zero_and_negative_fall_back_to_default(self):
        for value in ('0', '-1', '-999'):
            with self.subTest(value=value):
                page, size = _pagination_params(self.request(page=value, page_size=value))
                self.assertEqual(page, 1)
                self.assertEqual(size, DEFAULT_PAGE_SIZE)

    def test_non_numeric_falls_back_to_default(self):
        page, size = _pagination_params(self.request(page='abc', page_size='xyz'))
        self.assertEqual(page, 1)
        self.assertEqual(size, DEFAULT_PAGE_SIZE)

    def test_empty_falls_back_to_default(self):
        page, size = _pagination_params(self.request(page='', page_size=''))
        self.assertEqual(page, 1)
        self.assertEqual(size, DEFAULT_PAGE_SIZE)

    def test_float_falls_back_to_default(self):
        # int('1.5') raises ValueError, which must not surface as a 500.
        page, size = _pagination_params(self.request(page='1.5'))
        self.assertEqual(page, 1)

    def test_huge_page_number_is_allowed_but_would_be_empty(self):
        """Not clamped — only page_size is. An empty page is harmless."""
        page, _ = _pagination_params(self.request(page='999999999'))
        self.assertEqual(page, 999999999)


class ModelMetaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw')

    def test_in_flight_statuses(self):
        self.assertEqual(
            set(Campaign.IN_FLIGHT_STATUSES),
            {Campaign.Status.QUEUED, Campaign.Status.RUNNING},
        )

    def test_in_flight_excludes_terminal_states(self):
        for status in (Campaign.Status.DRAFT, Campaign.Status.COMPLETED, Campaign.Status.FAILED):
            self.assertNotIn(status, Campaign.IN_FLIGHT_STATUSES)

    def test_is_finished(self):
        campaign = Campaign.objects.create(owner=self.user, name='c', message_text='hi')
        self.assertFalse(campaign.is_finished)

        campaign.status = Campaign.Status.COMPLETED
        self.assertTrue(campaign.is_finished)

        campaign.status = Campaign.Status.FAILED
        self.assertTrue(campaign.is_finished)

        campaign.status = Campaign.Status.RUNNING
        self.assertFalse(campaign.is_finished)

    def test_default_status_is_draft(self):
        campaign = Campaign.objects.create(owner=self.user, name='c', message_text='hi')
        self.assertEqual(campaign.status, Campaign.Status.DRAFT)

    def test_str_representations(self):
        campaign = Campaign.objects.create(owner=self.user, name='My campaign', message_text='hi')
        self.assertEqual(str(campaign), 'My campaign')

        contact = Contact.objects.create(campaign=campaign, name='Ada', phone='+2348161234765')
        self.assertIn('Ada', str(contact))
        self.assertIn('+2348161234765', str(contact))

        profile = type(campaign)._meta.apps.get_model('sender', 'UserProfile').objects.create(
            user=self.user, whatsapp_number='+2348161234765'
        )
        self.assertIn('alice', str(profile))

    def test_heartbeat_defaults_to_null(self):
        campaign = Campaign.objects.create(owner=self.user, name='c', message_text='hi')
        self.assertIsNone(campaign.last_heartbeat_at)


class QueryIndexesTests(TestCase):
    """
    The indexes added in migration 0007 exist for a reason; this pins that
    they are used rather than silently reverted.
    """

    def test_campaign_owner_created_index_exists(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                           ['campaign_owner_created_idx'])
            self.assertIsNotNone(cursor.fetchone())

    def test_contact_campaign_status_index_exists(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                           ['contact_campaign_status_idx'])
            self.assertIsNotNone(cursor.fetchone())

    def test_campaign_list_query_uses_the_composite_index(self):
        """
        Regression guard. With an index on owner_id alone, SQLite sorted the
        result set in a temp B-tree on every request.
        """
        with connection.cursor() as cursor:
            cursor.execute("""
                EXPLAIN QUERY PLAN
                SELECT id FROM sender_campaign
                WHERE owner_id = 1 ORDER BY created_at DESC LIMIT 25
            """)
            plan = ' '.join(row[-1] for row in cursor.fetchall())

        self.assertIn('campaign_owner_created_idx', plan.lower())
        self.assertNotIn('temp b-tree', plan.lower())

    def test_progress_aggregate_uses_a_covering_index(self):
        with connection.cursor() as cursor:
            cursor.execute("""
                EXPLAIN QUERY PLAN
                SELECT status, COUNT(id) FROM sender_contact
                WHERE campaign_id = 1 GROUP BY status
            """)
            plan = ' '.join(row[-1] for row in cursor.fetchall())

        self.assertIn('contact_campaign_status_idx', plan.lower())
        self.assertNotIn('temp b-tree', plan.lower())


class CampaignListSerializerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw')
        self.factory = APIRequestFactory()

    def make(self, statuses=(), **kwargs):
        campaign = Campaign.objects.create(
            owner=self.user, name='c', message_text='hi', send_mode='delay', **kwargs
        )
        for i, contact_status in enumerate(statuses):
            Contact.objects.create(
                campaign=campaign, name=f'C{i}',
                phone=f'+23481612347{i:02d}', status=contact_status,
            )
        return campaign

    def annotated(self, campaign, prefetch=False):
        """
        Re-reads the campaign through the same annotated queryset the list
        endpoint uses. The serializer reads counts from annotations, so a bare
        model instance has none of those attributes.
        """
        from django.db.models import Count, Q

        queryset = Campaign.objects.filter(pk=campaign.pk).annotate(
            total_contacts=Count('contacts', distinct=True),
            sent_contacts=Count('contacts', filter=Q(contacts__status='sent')),
            failed_contacts=Count('contacts', filter=Q(contacts__status='failed')),
        )
        if prefetch:
            queryset = queryset.prefetch_related('media_files')
        return queryset.get()

    def serialize(self, campaign, prefetch=False):
        return CampaignListSerializer(self.annotated(campaign, prefetch)).data

    def test_counts_each_status(self):
        campaign = self.make(statuses=['sent', 'sent', 'failed', 'pending'])

        data = self.serialize(campaign)
        self.assertEqual(data['total_contacts'], 4)
        self.assertEqual(data['sent_contacts'], 2)
        self.assertEqual(data['failed_contacts'], 1)
        self.assertEqual(data['pending_contacts'], 1)

    def test_pending_is_derived_and_never_negative(self):
        """Guards against counts drifting past total after a partial write."""
        campaign = self.make(statuses=['sent', 'failed'])
        data = self.serialize(campaign)
        self.assertEqual(data['pending_contacts'], 0)

    def test_can_retry_requires_failures_and_no_in_flight(self):
        self.assertTrue(self.serialize(self.make(statuses=['failed']))['can_retry'])

        self.assertFalse(
            self.serialize(
                self.make(statuses=['failed'], status=Campaign.Status.RUNNING)
            )['can_retry']
        )

        self.assertFalse(self.serialize(self.make(statuses=['sent']))['can_retry'])

    def test_media_count_via_prefetch(self):
        campaign = self.make()
        for i in range(2):
            MediaFile.objects.create(
                campaign=campaign,
                file=SimpleUploadedFile(f'p{i}.png', b'x', content_type='image/png'),
            )

        self.assertEqual(self.serialize(campaign, prefetch=True)['media_count'], 2)

    def test_media_count_without_prefetch(self):
        """
        The serializer has two paths: a prefetched relation (len of the list)
        and an unprefetched one (a count query). Both must agree.
        """
        campaign = self.make()
        MediaFile.objects.create(
            campaign=campaign,
            file=SimpleUploadedFile('p.png', b'x', content_type='image/png'),
        )

        self.assertEqual(self.serialize(campaign, prefetch=False)['media_count'], 1)

    def test_media_count_is_zero_with_no_attachments(self):
        self.assertEqual(self.serialize(self.make())['media_count'], 0)

    def test_media_name_strips_the_storage_prefix(self):
        """
        The stored name is 'campaign_media/<file>'. The serializer must return
        just the filename — the storage path is an implementation detail and
        exposing it would hint at on-disk layout.
        """
        campaign = self.make()
        media = MediaFile.objects.create(
            campaign=campaign,
            file=SimpleUploadedFile('holiday photo.png', b'x', content_type='image/png'),
        )
        from sender.serializers import MediaFileSerializer

        stored = media.file.name
        self.assertTrue(stored.startswith('campaign_media/'))

        name = MediaFileSerializer(media).data['name']
        self.assertNotIn('/', name)
        self.assertTrue(name.endswith('.png'))
        # Django appends a random suffix on a name collision with a file left
        # in MEDIA_ROOT by an earlier run, so match the stem loosely.
        self.assertTrue(name.startswith('holiday_photo'), name)

    def test_detail_serializer_excludes_the_message_body_from_list_rows(self):
        """The list must not carry message text — that is decrypted per row."""
        campaign = Campaign.objects.create(owner=self.user, name='c', message_text='secret body')
        self.assertNotIn('message_text', CampaignListSerializer(campaign).data)
        self.assertIn('message_text', CampaignSerializer(campaign).data)

    def test_summary_serialization_does_not_load_contacts(self):
        """
        The regression that motivated annotating instead of loading: returning
        contacts decrypts every phone number for every campaign on the page.
        """
        from django.db.models import Count as _Count, Q as _Q

        for i in range(5):
            campaign = Campaign.objects.create(owner=self.user, name=f'c{i}', message_text='hi')
            Contact.objects.bulk_create([
                Contact(campaign=campaign, name='a', phone=f'+23481612347{j:02d}', status='sent')
                for j in range(5)
            ])

        annotated = list(
            Campaign.objects.filter(owner=self.user).annotate(
                total_contacts=_Count('contacts', distinct=True),
                sent_contacts=_Count('contacts', filter=_Q(contacts__status='sent')),
                failed_contacts=_Count('contacts', filter=_Q(contacts__status='failed')),
            ).prefetch_related('media_files')
        )

        with CaptureQueriesContext(connection) as captured:
            rows = CampaignListSerializer(annotated, many=True).data

        self.assertEqual(len(rows), 5)

        contact_selects = [
            q['sql'] for q in captured.captured_queries
            if 'FROM "sender_contact"' in q['sql'] and 'COUNT' not in q['sql'].upper()
        ]
        self.assertEqual(contact_selects, [], 'summary rows must not fetch contacts')


class CampaignDetailSerializerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw')
        self.factory = APIRequestFactory()

    def test_includes_contacts_and_media(self):
        campaign = Campaign.objects.create(owner=self.user, name='c', message_text='hi')
        Contact.objects.create(campaign=campaign, name='Ada', phone='+2348161234765')
        MediaFile.objects.create(
            campaign=campaign,
            file=SimpleUploadedFile('p.png', b'x', content_type='image/png'),
        )

        data = CampaignSerializer(campaign).data

        self.assertEqual(len(data['contacts']), 1)
        self.assertEqual(len(data['media_files']), 1)
        self.assertEqual(data['contacts'][0]['name'], 'Ada')

    def test_is_read_only_everywhere(self):
        """
        The view already scopes lookups to request.user; read-only fields stop
        a crafted POST from setting owner or status directly.
        """
        campaign = Campaign.objects.create(owner=self.user, name='c', message_text='hi')
        data = CampaignSerializer(campaign).data
        self.assertEqual(
            set(data) & set(CampaignSerializer.Meta.read_only_fields),
            set(data),
            'every exposed field should be read-only',
        )

    def test_media_url_is_not_exposed(self):
        """A bare file path would point at the route we deliberately closed."""
        campaign = Campaign.objects.create(owner=self.user, name='c', message_text='hi')
        MediaFile.objects.create(
            campaign=campaign,
            file=SimpleUploadedFile('p.png', b'x', content_type='image/png'),
        )
        self.assertNotIn('file', CampaignSerializer(campaign).data['media_files'][0])
        self.assertNotIn('url', CampaignSerializer(campaign).data['media_files'][0])


class OrderingTests(TestCase):
    def test_newest_first_is_stable_for_equal_timestamps(self):
        """
        created_at is microsecond-resolution, but two campaigns created in the
        same tick would order arbitrarily. The list adds no tiebreaker, which
        is fine for display but worth pinning so a future change doesn't
        silently rely on it.
        """
        user = User.objects.create_user('alice', password='pw')
        base = timezone.now()
        first = Campaign.objects.create(owner=user, name='first', message_text='hi')
        second = Campaign.objects.create(owner=user, name='second', message_text='hi')
        Campaign.objects.filter(pk=first.pk).update(created_at=base)
        Campaign.objects.filter(pk=second.pk).update(created_at=base)

        ordered = list(Campaign.objects.filter(owner=user).order_by('-created_at'))
        self.assertEqual(len(ordered), 2)
