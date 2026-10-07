"""
Tests for the rekey_contacts management command.

This exists because key rotation was documented as a supported, safe operation
with no way to actually perform it. The tests therefore cover the two cases
that matter: a real rotation where rows must move to the new primary key, and
the case that motivated using raw SQL — a row no configured key can read must be
reported, not crash the run, and must not be deleted.
"""

from io import StringIO

from cryptography.fernet import Fernet
from django.core.management import call_command
from django.db import connection
from django.test import TestCase

from sender.models import Campaign, Contact, UserProfile

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()


def raw_value(table, column, pk):
    """Reads ciphertext without going through the ORM/decrypt path."""
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {column} FROM {table} WHERE id = %s', [pk])
        row = cursor.fetchone()
    return row[0] if row else None


def set_raw(table, column, pk, value):
    """
    Writes ciphertext directly.

    A plain .update() would go through get_prep_value and encrypt the value
    again, so tests that need to place specific ciphertext in a row must bypass
    the field.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            f'UPDATE {table} SET {column} = %s WHERE id = %s', [value, pk]
        )


def set_keys(*keys):
    from django.conf import settings

    settings.CRYPTOGRAPHY_KEYS = [k.encode() for k in keys]


class RekeyTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from sender import encryption

        self._original = encryption._fernet_cache
        encryption._fernet_cache = {}
        self.addCleanup(self._restore_cache, encryption)

        self.user = User.objects.create_user('alice', password='pw')
        self.profile = UserProfile.objects.create(
            user=self.user, whatsapp_number='+2348161234765'
        )
        self.campaign = Campaign.objects.create(
            owner=self.user, name='c', message_text='secret body', send_mode='delay'
        )
        self.contact = Contact.objects.create(
            campaign=self.campaign, name='A', phone='+2348161234765'
        )

        self._original_keys = None
        from django.conf import settings

        self._original_keys = settings.CRYPTOGRAPHY_KEYS
        self.addCleanup(self._restore_keys, settings)

    def _restore_cache(self, encryption):
        encryption._fernet_cache = self._original

    def _restore_keys(self, settings):
        settings.CRYPTOGRAPHY_KEYS = self._original_keys

    def run_command(self, *args):
        out = StringIO()
        call_command('rekey_contacts', *args, stdout=out)
        return out.getvalue()

    def test_rewrites_rows_onto_the_new_primary_key(self):
        """
        The whole point: a row written under the old key must end up readable
        by the new key alone, so the old one can be dropped.
        """
        set_keys(KEY_A)
        Contact.objects.filter(pk=self.contact.pk).update(phone='+2348161234799')
        Campaign.objects.filter(pk=self.campaign.pk).update(message_text='rotated body')
        before = raw_value('sender_contact', 'phone', self.contact.pk)

        set_keys(KEY_B, KEY_A)
        self.run_command()

        after = raw_value('sender_contact', 'phone', self.contact.pk)
        self.assertNotEqual(before, after, 'ciphertext should have been rewritten')

        # Readable by the new key on its own — the old key is now droppable.
        set_keys(KEY_B)
        self.assertEqual(
            Contact.objects.get(pk=self.contact.pk).phone,
            '+2348161234799',
            'row should decrypt with the new key alone after rekeying',
        )

    def test_rewrites_every_encrypted_field(self):
        set_keys(KEY_A)
        set_keys(KEY_B, KEY_A)
        self.run_command()
        output = self.run_command()

        self.assertIn('contact phone', output)
        self.assertIn('campaign message', output)
        self.assertIn('profile whatsapp number', output)

    def test_dry_run_changes_nothing(self):
        set_keys(KEY_A)
        Contact.objects.filter(pk=self.contact.pk).update(phone='+2348161234799')
        before = raw_value('sender_contact', 'phone', self.contact.pk)

        set_keys(KEY_B, KEY_A)
        self.run_command('--dry-run')

        self.assertEqual(raw_value('sender_contact', 'phone', self.contact.pk), before)
        self.assertIn('Dry run', self.run_command('--dry-run'))

    def test_rows_already_on_the_primary_key_are_left_alone(self):
        """No point rewriting them, and it keeps the command idempotent."""
        set_keys(KEY_A)
        set_keys(KEY_A, KEY_B)
        before = raw_value('sender_contact', 'phone', self.contact.pk)

        self.run_command()

        self.assertEqual(raw_value('sender_contact', 'phone', self.contact.pk), before)

    def test_unreadable_row_is_reported_not_fatal(self):
        """
        A row encrypted under a key nobody has any more. The command must name
        it and carry on — this is exactly the case that made the ORM unusable,
        because decrypt_value raises from from_db_value on any queryset read.
        """
        orphan = Fernet.generate_key().decode()
        cipher = Fernet(orphan.encode()).encrypt(b'+2349999999999').decode()
        set_raw('sender_contact', 'phone', self.contact.pk, cipher)

        set_keys(KEY_A)
        output = self.run_command()

        self.assertIn('could not be decrypted', output)
        self.assertIn('Nothing was deleted automatically', output)
        # The row is still there, untouched.
        self.assertEqual(
            raw_value('sender_contact', 'phone', self.contact.pk), cipher
        )

    def test_unreadable_row_does_not_block_readable_ones(self):
        """One bad row must not abandon the migration for everything else."""
        orphan = Fernet.generate_key().decode()
        cipher = Fernet(orphan.encode()).encrypt(b'+2349999999999').decode()
        second = Contact.objects.create(
            campaign=self.campaign, name='B', phone='+2348161234700'
        )
        set_raw('sender_contact', 'phone', self.contact.pk, cipher)

        set_keys(KEY_A)
        Contact.objects.filter(pk=second.pk).update(phone='+2348161234701')
        self.run_command()

        set_keys(KEY_B, KEY_A)
        self.run_command()
        set_keys(KEY_B)
        self.assertEqual(
            Contact.objects.get(pk=second.pk).phone,
            '+2348161234701',
            'the readable row should still have been migrated',
        )

    def test_is_idempotent(self):
        set_keys(KEY_A)
        set_keys(KEY_B, KEY_A)
        self.run_command()
        first = raw_value('sender_contact', 'phone', self.contact.pk)
        self.run_command()
        self.assertEqual(raw_value('sender_contact', 'phone', self.contact.pk), first)

    def test_single_key_run_says_so(self):
        """A rekey with one key configured is almost always a mistake."""
        set_keys(KEY_A)
        output = self.run_command()
        self.assertIn('Only one key is configured', output)
