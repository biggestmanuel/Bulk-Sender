"""Tests for the encrypted model fields and key rotation."""

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, override_settings

from sender.encryption import EncryptedCharField, decrypt_value, encrypt_value
from sender.models import Campaign, Contact, UserProfile


def read_raw_message(campaign_id):
    """Reads the stored column directly, bypassing field decryption."""
    with connection.cursor() as cursor:
        cursor.execute('SELECT message_text FROM sender_campaign WHERE id = %s', [campaign_id])
        return cursor.fetchone()[0]


def read_raw_phone(contact_id):
    with connection.cursor() as cursor:
        cursor.execute('SELECT phone FROM sender_contact WHERE id = %s', [contact_id])
        return cursor.fetchone()[0]


class EncryptionRoundTripTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw')

    def test_message_text_round_trips(self):
        secret = 'Hi {name}, your appointment is on Tuesday at 14:30.'
        campaign = Campaign.objects.create(
            owner=self.user, name='c', message_text=secret, send_mode='delay'
        )
        self.assertEqual(Campaign.objects.get(pk=campaign.pk).message_text, secret)

    def test_phone_round_trips(self):
        campaign = Campaign.objects.create(owner=self.user, name='c', message_text='hi')
        contact = Contact.objects.create(campaign=campaign, name='Ada', phone='+2348161234765')
        self.assertEqual(Contact.objects.get(pk=contact.pk).phone, '+2348161234765')

    def test_ciphertext_is_actually_stored(self):
        """The whole point: plaintext must not sit in the database."""
        campaign = Campaign.objects.create(
            owner=self.user, name='c', message_text='super-secret-body', send_mode='delay'
        )

        stored = read_raw_message(campaign.pk)

        self.assertNotEqual(stored, 'super-secret-body')
        self.assertNotIn('super-secret', stored)
        self.assertEqual(decrypt_value(stored), 'super-secret-body')

    def test_phone_ciphertext_is_stored(self):
        campaign = Campaign.objects.create(owner=self.user, name='c', message_text='hi')
        contact = Contact.objects.create(campaign=campaign, name='Ada', phone='+2348161234765')

        stored = read_raw_phone(contact.pk)
        self.assertNotIn('2348161234765', stored)
        self.assertEqual(decrypt_value(stored), '+2348161234765')

    def test_whatsapp_number_round_trips(self):
        UserProfile.objects.create(user=self.user, whatsapp_number='+2348161234765')
        self.assertEqual(UserProfile.objects.get(user=self.user).whatsapp_number, '+2348161234765')

    def test_blank_value_passes_through(self):
        UserProfile.objects.create(user=self.user, whatsapp_number='')
        self.assertEqual(UserProfile.objects.get(user=self.user).whatsapp_number, '')

    def test_helpers_pass_through_empty(self):
        self.assertEqual(encrypt_value(''), '')
        self.assertIsNone(encrypt_value(None))
        self.assertEqual(decrypt_value(''), '')

    def test_invalid_key_is_rejected_loudly(self):
        """A typo in the env var must fail at write time, not silently."""
        with override_settings(CRYPTOGRAPHY_KEYS=[b'not-a-real-fernet-key']):
            with self.assertRaises(ValueError):
                encrypt_value('anything')

    def test_no_keys_configured_raises(self):
        with override_settings(CRYPTOGRAPHY_KEYS=[]):
            with self.assertRaises(ValueError):
                encrypt_value('x')


class KeyRotationTests(TestCase):
    """
    Writes use key[0]; reads try every key.

    This is the property that lets an operator prepend a new key and drop the
    old one later without a window where existing rows become unreadable.
    """

    KEY_A = Fernet.generate_key().decode().encode()
    KEY_B = Fernet.generate_key().decode().encode()

    def setUp(self):
        self.user = User.objects.create_user('bob', password='pw')

    def test_old_ciphertext_reads_after_rotation(self):
        with override_settings(CRYPTOGRAPHY_KEYS=[self.KEY_A]):
            campaign = Campaign.objects.create(owner=self.user, name='c', message_text='legacy')
        legacy_ciphertext = read_raw_message(campaign.pk)

        # Prepend a new key, keep the old one for reads.
        with override_settings(CRYPTOGRAPHY_KEYS=[self.KEY_B, self.KEY_A]):
            self.assertEqual(Campaign.objects.get(pk=campaign.pk).message_text, 'legacy')

            # The stored bytes are untouched by rotation.
            self.assertEqual(read_raw_message(campaign.pk), legacy_ciphertext)

            # New writes use the new key only.
            fresh = Campaign.objects.create(owner=self.user, name='c2', message_text='fresh')
            fresh_ciphertext = read_raw_message(fresh.pk)
            self.assertNotEqual(fresh_ciphertext, legacy_ciphertext)
            self.assertEqual(decrypt_value(fresh_ciphertext), 'fresh')
            self.assertEqual(Campaign.objects.get(pk=fresh.pk).message_text, 'fresh')

    def test_dropping_a_key_fails_with_an_actionable_error(self):
        with override_settings(CRYPTOGRAPHY_KEYS=[self.KEY_A]):
            campaign = Campaign.objects.create(owner=self.user, name='c', message_text='doomed')

        with override_settings(CRYPTOGRAPHY_KEYS=[self.KEY_B]):
            with self.assertRaises(ValueError) as ctx:
                Campaign.objects.get(pk=campaign.pk).message_text
        self.assertIn('CRYPTOGRAPHY_KEYS', str(ctx.exception))

    def test_three_key_rotation_ordering(self):
        key_c = Fernet.generate_key().decode().encode()
        with override_settings(CRYPTOGRAPHY_KEYS=[self.KEY_A]):
            campaign = Campaign.objects.create(owner=self.user, name='c', message_text='gen1')

        with override_settings(CRYPTOGRAPHY_KEYS=[self.KEY_B, self.KEY_A]):
            Campaign.objects.get(pk=campaign.pk).message_text

        with override_settings(CRYPTOGRAPHY_KEYS=[key_c, self.KEY_B, self.KEY_A]):
            self.assertEqual(Campaign.objects.get(pk=campaign.pk).message_text, 'gen1')


class EncryptedFieldDefinitionTests(TestCase):
    def test_char_field_maps_to_text_column(self):
        # Ciphertext is longer than plaintext, so the column must not be
        # VARCHAR(20) or every save would overflow it.
        self.assertEqual(EncryptedCharField(max_length=20).db_type(None), 'text')

    def test_max_length_validates_plaintext(self):
        user = User.objects.create_user('carol', password='pw')
        campaign = Campaign.objects.create(owner=user, name='c', message_text='hi')

        too_long = Contact(campaign=campaign, name='n', phone='x' * 5000)
        with self.assertRaises(Exception):
            too_long.full_clean()

    def test_admin_field_does_not_double_encrypt(self):
        """Round-tripping through a ModelForm must not re-encrypt ciphertext."""
        from django import forms

        from sender.models import Contact as ContactModel

        class ContactForm(forms.ModelForm):
            class Meta:
                model = ContactModel
                fields = ['name', 'phone']

        user = User.objects.create_user('dave', password='pw')
        campaign = Campaign.objects.create(owner=user, name='c', message_text='hi')
        contact = ContactModel.objects.create(campaign=campaign, name='Ada', phone='+2348161234765')

        form = ContactForm(instance=contact)
        self.assertEqual(form.initial['phone'], '+2348161234765')

        form = ContactForm({'name': 'Ada L', 'phone': '+2348161234765'}, instance=contact)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(ContactModel.objects.get(pk=contact.pk).phone, '+2348161234765')