"""
Re-encrypts stored phone numbers and message bodies under the current
primary encryption key.

    python manage.py rekey_contacts              # report and re-encrypt
    python manage.py rekey_contacts --dry-run    # report only

Why this exists
---------------
settings.CRYPTOGRAPHY_KEYS is newest-first: writes always use key 0, reads try
every key in turn. That makes *reading* across a rotation safe, but it leaves a
gap. Rows are only re-encrypted when something explicitly saves them, which for
a campaign that finished sending is never. So the documented rotation —
prepend a key, migrate, then drop the old one — had no way to actually run the
middle step, and dropping the old key first would have made every un-migrated
row permanently unreadable.

This is that middle step.

Reading raw ciphertext, not through the ORM
-------------------------------------------
Deliberate. sender.encryption.decrypt_value raises when no configured key
matches, and that exception fires from from_db_value on *any* queryset read —
which means a single unreadable row makes Campaign.objects.all() unusable,
including .delete(), because Django's cascade collector has to fetch rows to
resolve relations. A migration tool that cannot run because of the very rows it
exists to fix is useless, so this reads ciphertext with raw SQL and handles
per-row failures itself.

Rows that no configured key can decrypt are reported and left alone. This
command never deletes data.
"""

from django.core.management.base import BaseCommand
from django.db import connection

from sender.encryption import _get_fernets

# (table, column, label). Kept explicit rather than introspected so a schema
# change shows up here as a reviewable diff instead of silently rekeying
# whatever happens to be on the model.
ENCRYPTED_COLUMNS = [
    ('sender_contact', 'phone', 'contact phone'),
    ('sender_campaign', 'message_text', 'campaign message'),
    ('sender_userprofile', 'whatsapp_number', 'profile whatsapp number'),
]


class Command(BaseCommand):
    help = (
        'Re-encrypt stored phone numbers and message bodies under the current '
        'primary CRYPTOGRAPHY_KEYS entry. Run after prepending a new key, '
        'before dropping the old one.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would change without writing anything.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        fernets = _get_fernets()
        primary = fernets[0]

        self.stdout.write(
            self.style.MIGRATE_HEADING('Rekey encrypted fields')
        )
        self.stdout.write(
            f'{len(fernets)} key(s) configured; writing with the primary one.'
        )
        if len(fernets) == 1:
            self.stdout.write(
                self.style.WARNING(
                    'Only one key is configured. Rekeying now is a no-op unless '
                    'rows were written under a key that has since been removed — '
                    'in which case they will be listed as unreadable.'
                )
            )
        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run: nothing will be written.'))

        total_rewritten = 0
        total_unreadable = 0

        with connection.cursor() as cursor:
            for table, column, label in ENCRYPTED_COLUMNS:
                cursor.execute(
                    f'SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL '
                    f'AND {column} != %s',
                    [''],
                )
                rows = cursor.fetchall()

                rewritten = 0
                unreadable = []
                for pk, stored in rows:
                    if stored is None or stored == '':
                        continue

                    raw = stored.encode() if isinstance(stored, str) else stored

                    # Already on the primary key: nothing to do. Trying the
                    # primary first means the common case costs one decrypt.
                    if self._decrypts(primary, raw):
                        continue

                    plaintext = None
                    for fernet in fernets[1:]:
                        plaintext = self._try_decrypt(fernet, raw)
                        if plaintext is not None:
                            break

                    if plaintext is None:
                        unreadable.append(pk)
                        continue

                    if not dry_run:
                        new_value = primary.encrypt(plaintext).decode()
                        cursor.execute(
                            f'UPDATE {table} SET {column} = %s WHERE id = %s',
                            [new_value, pk],
                        )
                    rewritten += 1

                total_rewritten += rewritten
                total_unreadable += len(unreadable)

                status = 'would be re-encrypted' if dry_run else 're-encrypted'
                line = f'  {label:<28} {rewritten} row(s) {status}'
                self.stdout.write(
                    self.style.SUCCESS(line) if rewritten else line
                )

                if unreadable:
                    shown = unreadable[:20]
                    more = f' (+{len(unreadable) - len(shown)} more)' if len(unreadable) > len(shown) else ''
                    self.stdout.write(
                        self.style.ERROR(
                            f'  {label:<28} {len(unreadable)} row(s) could not be '
                            f'decrypted by ANY configured key and were left alone: '
                            f'{shown}{more}'
                        )
                    )

        self.stdout.write('')
        if total_unreadable:
            self.stdout.write(
                self.style.ERROR(
                    f'{total_unreadable} row(s) are unreadable with the current keys. '
                    'The encryption key for them is gone. They cannot be recovered '
                    'by this command — restore the missing key from your password '
                    'manager, or delete those rows by hand if the data is disposable. '
                    'Nothing was deleted automatically.'
                )
            )
        if dry_run and total_rewritten:
            self.stdout.write(
                self.style.WARNING('This was a dry run. Re-run without --dry-run to apply.')
            )
        elif not dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Done. {total_rewritten} row(s) re-encrypted under the primary key.'
                )
            )

    @staticmethod
    def _try_decrypt(fernet, raw):
        try:
            return fernet.decrypt(raw)
        except Exception:
            return None

    @classmethod
    def _decrypts(cls, fernet, raw):
        return cls._try_decrypt(fernet, raw) is not None
