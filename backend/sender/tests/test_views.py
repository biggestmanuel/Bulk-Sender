"""
API tests.

Focus is on the behaviours that were broken or missing:
  * campaign creation no longer silently dropping contacts
  * per-user data isolation
  * send-start guards (duplicate start, quota, missing phone number)
  * media no longer being publicly readable
  * auth errors that the frontend can actually act on
"""

import json

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from sender.models import Campaign, Contact, UserProfile, WhatsAppLoginState


class AuthTestCase(TestCase):
    """
    Base class for API tests.

    Two pieces of global state have to be reset between tests, or tests
    interfere with each other in ways that look like product bugs:

      * the throttle cache — LocMemCache outlives the per-test database, so
        without this every test after the fifth registration would be
        throttled.
      * the in-flight send registry in sender.views, whose keys are
        (campaign_id, user_id) pairs — and ids restart at 1 for each test's
        fresh database.
    """

    def setUp(self):
        from django.core.cache import cache

        from sender import views

        cache.clear()
        views._active_sends.clear()
        self.client = APIClient()

    def register(self, username='alice', password='correct-horse-1'):
        response = self.client.post(
            '/api/auth/register/',
            {'username': username, 'password': password, 'email': ''},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['token']


class RegisterTests(AuthTestCase):
    def test_creates_user_and_returns_token(self):
        response = self.client.post(
            '/api/auth/register/',
            {'username': 'alice', 'password': 'correct-horse-1'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertIn('token', response.data)
        self.assertEqual(response.data['username'], 'alice')
        self.assertTrue(User.objects.filter(username='alice').exists())

    def test_password_is_hashed_not_stored(self):
        self.register()
        user = User.objects.get(username='alice')
        self.assertNotEqual(user.password, 'correct-horse-1')
        self.assertTrue(user.check_password('correct-horse-1'))

    def test_duplicate_username_rejected(self):
        self.register()
        response = self.client.post(
            '/api/auth/register/',
            {'username': 'alice', 'password': 'another-one-1'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.data)

    def test_missing_fields_rejected(self):
        response = self.client.post(
            '/api/auth/register/', {'username': 'bob'}, format='json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.data)

    def test_registration_creates_a_profile_row(self):
        self.register('carol')
        self.assertTrue(UserProfile.objects.filter(user__username='carol').exists())


class LoginTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user('alice', password='correct-horse-1')

    def test_valid_login_returns_token(self):
        response = self.client.post(
            '/api/auth/login/',
            {'username': 'alice', 'password': 'correct-horse-1'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['username'], 'alice')
        self.assertTrue(response.data['token'])

    def test_wrong_password_rejected(self):
        response = self.client.post(
            '/api/auth/login/',
            {'username': 'alice', 'password': 'wrong'},
            format='json',
        )
        self.assertEqual(response.status_code, 401)

    def test_unknown_user_and_wrong_password_are_indistinguishable(self):
        """
        Identical status and body for both, so this endpoint can't be used to
        enumerate which usernames exist.
        """
        unknown = self.client.post(
            '/api/auth/login/', {'username': 'nobody', 'password': 'x'}, format='json'
        )
        wrong = self.client.post(
            '/api/auth/login/', {'username': 'alice', 'password': 'x'}, format='json'
        )
        self.assertEqual(unknown.status_code, wrong.status_code)
        self.assertEqual(unknown.data['error'], wrong.data['error'])

    def test_disabled_account_rejected(self):
        """
        401, not 403: Django's auth backend refuses inactive users before any
        user object reaches the view, and returning a distinct status for
        "disabled" would confirm the account exists.
        """
        self.user.is_active = False
        self.user.save()
        response = self.client.post(
            '/api/auth/login/',
            {'username': 'alice', 'password': 'correct-horse-1'},
            format='json',
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data['error'], 'Invalid username or password.')

    def test_login_is_rate_limited(self):
        """20 guesses at a 10/minute ceiling must trip the throttle."""
        for _ in range(20):
            self.client.post(
                '/api/auth/login/', {'username': 'alice', 'password': 'wrong'}, format='json'
            )
        response = self.client.post(
            '/api/auth/login/', {'username': 'alice', 'password': 'correct-horse-1'}, format='json'
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data['code'], 'rate_limited')

    def test_register_is_rate_limited(self):
        for i in range(10):
            self.client.post(
                '/api/auth/register/',
                {'username': f'user{i}', 'password': 'correct-horse-1'},
                format='json',
            )
        response = self.client.post(
            '/api/auth/register/', {'username': 'lastone', 'password': 'correct-horse-1'},
            format='json',
        )
        self.assertEqual(response.status_code, 429)


class AuthRequiredTests(AuthTestCase):
    def test_endpoints_require_a_token(self):
        for url in (
            '/api/profile/whatsapp-number/',
            '/api/campaigns/1/status/',
            '/api/login-status/',
        ):
            response = self.client.get(url)
            self.assertIn(response.status_code, (401, 403), url)

    def test_bad_token_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token not-a-real-token')
        self.assertEqual(self.client.get('/api/login-status/').status_code, 401)

    def test_session_cookie_is_not_accepted_as_auth(self):
        """
        The API is token-only. SessionAuthentication also enforces CSRF, which
        this frontend doesn't send, so leaving it enabled just produced
        confusing 403s on some deployments.
        """
        self.client.login(username='alice', password='correct-horse-1')
        response = self.client.get('/api/login-status/')
        self.assertIn(response.status_code, (401, 403))


class ProfileTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.register('alice')
        self.auth()

    def auth(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token}')

    def test_get_starts_blank(self):
        response = self.client.get('/api/profile/whatsapp-number/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['whatsapp_number'], '')

    def test_set_and_get_round_trip(self):
        response = self.client.put(
            '/api/profile/whatsapp-number/',
            {'whatsapp_number': '+2348161234765'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['whatsapp_number'], '+2348161234765')

        response = self.client.get('/api/profile/whatsapp-number/')
        self.assertEqual(response.data['whatsapp_number'], '+2348161234765')

    def test_number_is_normalized(self):
        response = self.client.put(
            '/api/profile/whatsapp-number/',
            {'whatsapp_number': '+234 816-123-4765'},
            format='json',
        )
        self.assertEqual(response.data['whatsapp_number'], '+2348161234765')

    def test_invalid_numbers_rejected(self):
        for bad in ('+2341', 'not-a-number', '08161234567', '+1234567890123'):
            response = self.client.put(
                '/api/profile/whatsapp-number/', {'whatsapp_number': bad}, format='json'
            )
            self.assertEqual(response.status_code, 400, bad)
            self.assertEqual(response.data['code'], 'invalid_phone', bad)

    def test_inherited_profile_is_not_leaked_between_users(self):
        self.client.put(
            '/api/profile/whatsapp-number/',
            {'whatsapp_number': '+2348161234765'},
            format='json',
        )

        other_token = self.register('mallory')
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {other_token}')
        response = self.client.get('/api/profile/whatsapp-number/')
        self.assertEqual(response.data['whatsapp_number'], '')

    def test_changing_number_clears_login_state(self):
        self.client.put(
            '/api/profile/whatsapp-number/',
            {'whatsapp_number': '+2348161234765'},
            format='json',
        )
        WhatsAppLoginState.objects.create(
            user=User.objects.get(username='alice'),
            phase=WhatsAppLoginState.Phase.WAITING_FOR_CODE,
            login_code='ABCD1234',
        )
        self.client.put(
            '/api/profile/whatsapp-number/',
            {'whatsapp_number': '+2348161234766'},
            format='json',
        )
        self.assertFalse(WhatsAppLoginState.objects.filter(user__username='alice').exists())


class CampaignCreationTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.register('alice')
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token}')
        self.user = User.objects.get(username='alice')

    def create(self, contacts, **extra):
        payload = {
            'name': 'Test campaign',
            'message_text': 'Hi {name}',
            'send_mode': 'delay',
            'contacts': json.dumps(contacts),
        }
        payload.update(extra)
        return self.client.post('/api/campaigns/create/', payload)

    def test_creates_campaign_with_contacts(self):
        response = self.create([
            {'name': 'Ada', 'phone': '+2348161234765'},
            {'name': 'Bob', 'phone': '+14155552671'},
        ])
        self.assertEqual(response.status_code, 201, response.data)
        campaign = Campaign.objects.get(pk=response.data['id'])
        self.assertEqual(campaign.owner, self.user)
        self.assertEqual(campaign.contacts.count(), 2)
        self.assertEqual(response.data['status'], 'draft')

    def test_padded_numbers_are_normalized(self):
        response = self.create([{'name': 'Ada', 'phone': '+234 816-123-4765'}])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            Campaign.objects.get(pk=response.data['id']).contacts.first().phone,
            '+2348161234765',
        )

    def test_invalid_contacts_are_reported_not_silently_dropped(self):
        """
        The original behaviour created a campaign from whatever validated and
        said nothing about the rest, so a user would watch a progress bar for
        a smaller list with no idea numbers had vanished.
        """
        response = self.create([
            {'name': 'Ada', 'phone': '+2348161234765'},
            {'name': 'Bad', 'phone': '+2341'},
            {'name': 'Worse', 'phone': 'nonsense'},
        ])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'invalid_contacts')
        self.assertEqual(response.data['rejected_count'], 2)
        self.assertEqual(Campaign.objects.count(), 0, 'nothing should be created')
        self.assertEqual(Contact.objects.count(), 0)

    def test_rejection_message_names_the_offending_numbers(self):
        response = self.create([
            {'name': 'Ada', 'phone': '+2348161234765'},
            {'name': 'Bad', 'phone': '+2341'},
        ])
        self.assertIn('+2341', response.data['error'])
        self.assertIn('1 of 2', response.data['error'])

    def test_rejection_examples_are_capped(self):
        contacts = [{'name': f'C{i}', 'phone': 'nonsense'} for i in range(50)]
        response = self.create(contacts)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(response.data['rejected']), 5)
        self.assertIn('and 45 more', response.data['error'])

    def test_empty_message_rejected(self):
        response = self.create([{'name': 'Ada', 'phone': '+2348161234765'}], message_text='')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'empty_message')

    def test_overlong_message_rejected(self):
        response = self.create(
            [{'name': 'Ada', 'phone': '+2348161234765'}],
            message_text='x' * 5000,
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_send_mode_rejected(self):
        response = self.create(
            [{'name': 'Ada', 'phone': '+2348161234765'}], send_mode='turbo'
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'invalid_send_mode')

    def test_empty_contact_list_rejected(self):
        response = self.create([])
        self.assertEqual(response.status_code, 400)

    def test_malformed_contacts_json_rejected(self):
        response = self.client.post('/api/campaigns/create/', {
            'name': 'x', 'message_text': 'y', 'send_mode': 'delay', 'contacts': '{not json',
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'bad_contacts')

    def test_contact_count_capped(self):
        response = self.create([
            {'name': 'C', 'phone': f'+2348161234{i:03d}'} for i in range(5001)
        ])
        self.assertEqual(response.status_code, 400)
        self.assertIn('Too many contacts', response.data['error'])

    def test_campaign_is_owned_by_creator(self):
        response = self.create([{'name': 'Ada', 'phone': '+2348161234765'}])
        self.assertEqual(Campaign.objects.get(pk=response.data['id']).owner, self.user)

    def test_duplicate_numbers_are_flagged_not_deduplicated(self):
        """
        The same person may legitimately be in a list twice (two numbers), and
        silently collapsing rows would be surprising. What matters is that the
        count reported back matches what was submitted.
        """
        response = self.create([
            {'name': 'Ada', 'phone': '+2348161234765'},
            {'name': 'Ada again', 'phone': '+2348161234765'},
        ])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['contacts'].__len__(), 2)


class CampaignMediaTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.register('alice')
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token}')
        self.user = User.objects.get(username='alice')

    def upload(self, content_type='image/png', size=64, name='pic.png'):
        payload = {
            'name': 'With media',
            'message_text': 'hi',
            'send_mode': 'delay',
            'contacts': json.dumps([{'name': 'Ada', 'phone': '+2348161234765'}]),
            'media_files': SimpleUploadedFile(
                name, b'x' * size, content_type=content_type
            ),
        }
        return self.client.post('/api/campaigns/create/', payload)

    def test_image_upload_succeeds(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data['media_files']), 1)
        # Django's storage appends a random suffix when the name collides with
        # a file left in MEDIA_ROOT by an earlier run, so match the extension.
        self.assertTrue(response.data['media_files'][0]['name'].endswith('.png'))

    def test_non_media_upload_rejected(self):
        response = self.upload(content_type='application/x-msdownload', name='evil.exe')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'media_bad_type')

    def test_oversized_upload_rejected(self):
        # MAX_MEDIA_BYTES is 1MB under the test settings, so this stays small.
        response = self.upload(size=2 * 1024 * 1024)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'media_too_large')

    def test_owner_can_download_own_media(self):
        response = self.upload()
        file_id = response.data['media_files'][0]['id']
        campaign_id = response.data['id']

        download = self.client.get(f'/api/campaigns/{campaign_id}/media/{file_id}/')
        self.assertEqual(download.status_code, 200)
        self.assertIn('attachment', download['Content-Disposition'])

    def test_other_user_cannot_download_media(self):
        """The bug this replaces: MEDIA_ROOT was served to the whole internet."""
        response = self.upload()
        file_id = response.data['media_files'][0]['id']
        campaign_id = response.data['id']

        other = self.register('mallory')
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {other}')
        download = self.client.get(f'/api/campaigns/{campaign_id}/media/{file_id}/')
        self.assertEqual(download.status_code, 404)

    def test_anonymous_cannot_download_media(self):
        response = self.upload()
        file_id = response.data['media_files'][0]['id']
        campaign_id = response.data['id']

        self.client.credentials()
        self.assertEqual(
            self.client.get(f'/api/campaigns/{campaign_id}/media/{file_id}/').status_code, 401
        )

    def test_media_url_is_not_under_public_media_prefix(self):
        """Serialization must not hand out a guessable public path."""
        response = self.upload()
        self.assertNotIn('url', response.data['media_files'][0])


class CampaignStatusTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.register('alice')
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token}')
        self.user = User.objects.get(username='alice')
        self.campaign = Campaign.objects.create(
            owner=self.user, name='c', message_text='hi', send_mode='delay'
        )
        Contact.objects.create(campaign=self.campaign, name='Ada', phone='+2348161234765', status='sent')
        Contact.objects.create(campaign=self.campaign, name='Bob', phone='+2348161234766', status='pending')

    def test_reports_progress(self):
        response = self.client.get(f'/api/campaigns/{self.campaign.pk}/status/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data['progress'],
            {'total': 2, 'sent': 1, 'failed': 0, 'pending': 1},
        )

    def test_reports_campaign_status(self):
        response = self.client.get(f'/api/campaigns/{self.campaign.pk}/status/')
        self.assertEqual(response.data['campaign_status'], 'draft')

    def test_other_user_cannot_read_status(self):
        other = self.register('mallory')
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {other}')
        response = self.client.get(f'/api/campaigns/{self.campaign.pk}/status/')
        self.assertEqual(response.status_code, 404)

    def test_missing_campaign_is_404_json(self):
        response = self.client.get('/api/campaigns/999999/status/')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data['code'], 'not_found')


class StartSendingTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.register('alice')
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token}')
        self.user = User.objects.get(username='alice')
        self.campaign = Campaign.objects.create(
            owner=self.user, name='c', message_text='hi', send_mode='delay'
        )
        Contact.objects.create(campaign=self.campaign, name='Ada', phone='+2348161234765')

    def set_phone(self, number='+2348161234765'):
        UserProfile.objects.update_or_create(
            user=self.user, defaults={'whatsapp_number': number}
        )

    def start(self, campaign=None):
        return self.client.post(f'/api/campaigns/{(campaign or self.campaign).pk}/start/')

    def test_requires_a_phone_number(self):
        response = self.start()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'phone_required')

    def test_start_moves_campaign_to_queued(self):
        self.set_phone()
        # The worker thread is stubbed out here; only the guard behaviour
        # is under test in this class.
        with patch_send_thread():
            response = self.start()
        self.assertEqual(response.status_code, 202)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.QUEUED)

    def test_second_start_is_rejected(self):
        """
        Two browsers against one persistent profile corrupt it. The old code
        let you POST /start/ as many times as you liked.
        """
        self.set_phone()
        with patch_send_thread():
            self.assertEqual(self.start().status_code, 202)
            second = self.start()
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.data['code'], 'already_sending')

    def test_cannot_start_two_campaigns_at_once(self):
        self.set_phone()
        other = Campaign.objects.create(
            owner=self.user, name='other', message_text='hi', send_mode='delay'
        )
        Contact.objects.create(campaign=other, name='X', phone='+2348161234767')

        with patch_send_thread():
            self.assertEqual(self.start().status_code, 202)
            response = self.start(other)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'user_busy')

    def test_cannot_start_other_users_campaign(self):
        self.set_phone()
        second = Campaign.objects.create(
            owner=self.user, name='second', message_text='hi', send_mode='delay'
        )
        Contact.objects.create(campaign=second, name='X', phone='+2348161234767')

        other = self.register('mallory')
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {other}')
        response = self.start(second)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data['code'], 'not_found')

    def test_rejects_campaign_with_nothing_pending(self):
        self.set_phone()
        Contact.objects.filter(campaign=self.campaign).update(status='sent')
        response = self.start()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'nothing_pending')

    def test_daily_quota_is_enforced(self):
        from django.test import override_settings

        self.set_phone()
        rates = dict(DEFAULT_THROTTLE_RATES={'send_quota': '1/day'})

        with patch_send_thread(), override_settings(REST_FRAMEWORK=rates):
            self.assertEqual(self.start().status_code, 202)

            # Mark the first send finished, otherwise the second start is
            # correctly rejected as user_busy before the quota is reached.
            Campaign.objects.filter(pk=self.campaign.pk).update(
                status=Campaign.Status.COMPLETED
            )

            second = Campaign.objects.create(
                owner=self.user, name='c2', message_text='hi', send_mode='delay'
            )
            Contact.objects.create(campaign=second, name='X', phone='+2348161234767')

            response = self.start(second)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data['code'], 'quota_exceeded')

    def test_quota_resets_next_day(self):
        from django.utils import timezone

        from sender.models import DailySendQuota

        quota = DailySendQuota.objects.create(
            user=self.user, date=timezone.localdate() - timezone.timedelta(days=1),
            contacts_used=999,
        )
        self.set_phone()
        with patch_send_thread():
            self.assertEqual(self.start().status_code, 202)
        quota.refresh_from_db()
        self.assertEqual(quota.contacts_used, 1)
        self.assertEqual(quota.date, timezone.localdate())


class LoginStatusTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.register('alice')
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token}')
        self.user = User.objects.get(username='alice')

    def test_idle_when_nothing_happening(self):
        response = self.client.get('/api/login-status/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['phase'], 'idle')
        self.assertFalse(response.data['code_ready'])
        self.assertIsNone(response.data['login_code'])

    def test_reports_a_pending_code(self):
        WhatsAppLoginState.objects.create(
            user=self.user,
            phase=WhatsAppLoginState.Phase.WAITING_FOR_CODE,
            login_code='ABCD1234',
        )
        response = self.client.get('/api/login-status/')
        self.assertTrue(response.data['code_ready'])
        self.assertEqual(response.data['login_code'], 'ABCD1234')

    def test_reports_slow_network(self):
        WhatsAppLoginState.objects.create(
            user=self.user, phase=WhatsAppLoginState.Phase.SLOW, login_code='ABCD1234',
        )
        response = self.client.get('/api/login-status/')
        self.assertTrue(response.data['login_slow'])
        self.assertEqual(response.data['login_code'], 'ABCD1234')

    def test_reports_failure_and_error(self):
        WhatsAppLoginState.objects.create(
            user=self.user,
            phase=WhatsAppLoginState.Phase.FAILED,
            error='selectors did not match',
        )
        response = self.client.get('/api/login-status/')
        self.assertTrue(response.data['login_failed'])
        self.assertEqual(response.data['login_code_error'], 'selectors did not match')
        self.assertIsNone(response.data['login_code'])

    def test_code_is_withheld_once_logged_in(self):
        """The code is single-use; it must not linger in the API after linking."""
        WhatsAppLoginState.objects.create(
            user=self.user,
            phase=WhatsAppLoginState.Phase.LOGGED_IN,
            login_code='ABCD1234',
        )
        response = self.client.get('/api/login-status/')
        self.assertFalse(response.data['code_ready'])
        self.assertIsNone(response.data['login_code'])

    def test_one_user_never_sees_another_users_code(self):
        WhatsAppLoginState.objects.create(
            user=self.user,
            phase=WhatsAppLoginState.Phase.WAITING_FOR_CODE,
            login_code='ALICE123',
        )

        other_token = self.register('mallory')
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {other_token}')
        response = self.client.get('/api/login-status/')
        self.assertIsNone(response.data['login_code'])

    def test_logged_in_row_is_created_on_first_poll(self):
        self.client.get('/api/login-status/')
        self.assertEqual(WhatsAppLoginState.objects.filter(user=self.user).count(), 1)


class SessionResetTests(AuthTestCase):
    def test_reset_clears_state(self):
        token = self.register('alice')
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token}')
        user = User.objects.get(username='alice')
        WhatsAppLoginState.objects.create(
            user=user, phase=WhatsAppLoginState.Phase.WAITING_FOR_CODE, login_code='ABCD1234'
        )

        response = self.client.post('/api/whatsapp-session/reset/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WhatsAppLoginState.objects.filter(user=user).exists())


def patch_send_thread():
    """
    Replaces the background sender thread with a no-op.

    Every test that exercises start_sending needs this: without it a real
    Chromium process is launched against WhatsApp.
    """
    from unittest.mock import patch

    from sender import views

    return patch.object(views.threading, 'Thread')