"""
Custom encrypted Django model fields, built directly on the `cryptography`
package instead of the unmaintained `django-cryptography` library (which
doesn't support Django 5+/6+ due to a removed Django internal it depended on).

Usage in models.py:
    from sender.encryption import EncryptedCharField, EncryptedTextField

    phone = EncryptedCharField(max_length=20)
    message_text = EncryptedTextField()

Data is encrypted before being saved to the database, and automatically
decrypted when read back through the model — no changes needed anywhere
else in the code that uses these fields.

Key rotation
------------
settings.CRYPTOGRAPHY_KEYS is an ordered list, newest key first:

  * writes always use CRYPTOGRAPHY_KEYS[0]
  * reads try every key in turn until one decrypts

So rotating is: prepend a new key to the environment variable, let the app
re-save rows (or run the rekey management command), then drop the old key
from the end. There is no window where existing data becomes unreadable,
which is what you get from a single-key scheme where rotating means either
a migration window or permanent data loss.
"""

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def _get_fernets():
    """
    Builds a Fernet per configured key, in settings order.

    These are valid 44-character base64 Fernet keys, each generated with:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

    Returns a list where index 0 is the primary (write) key. Raises
    ImproperlyConfigured-equivalent ValueError on a malformed key at first
    use, so a typo in the env var surfaces immediately rather than on the
    first row written.
    """
    keys = getattr(settings, 'CRYPTOGRAPHY_KEYS', None) or []
    if not keys:
        raise ValueError(
            'No CRYPTOGRAPHY_KEYS configured. Set the CRYPTOGRAPHY_KEYS '
            'environment variable before using encrypted fields.'
        )

    fernets = []
    for key in keys:
        if isinstance(key, str):
            key = key.encode()
        try:
            fernets.append(Fernet(key))
        except Exception as exc:
            raise ValueError(
                'CRYPTOGRAPHY_KEYS contains a value that is not a valid Fernet key. '
                'Generate one with: python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            ) from exc
    return fernets


def encrypt_value(value):
    """Encrypts a plaintext string with the primary key."""
    if value is None or value == '':
        return value
    return _get_fernets()[0].encrypt(str(value).encode()).decode()


def decrypt_value(value):
    """
    Decrypts a stored value, trying each configured key in turn.

    Raises a clear error (rather than returning garbage) when nothing
    decrypts — either the key is gone from the env var, or this row predates
    encryption being switched on.
    """
    if value is None or value == '':
        return value

    raw = value.encode() if isinstance(value, str) else value
    last_error = None
    for fernet in _get_fernets():
        try:
            return fernet.decrypt(raw).decode()
        except InvalidToken as exc:
            last_error = exc

    raise ValueError(
        "Could not decrypt a stored value — none of the keys in CRYPTOGRAPHY_KEYS "
        "match it. Either a key was removed from the environment variable or this "
        "row was written before encryption was enabled."
    ) from last_error


class EncryptedFieldMixin:
    """
    Shared logic for encrypting on save and decrypting on read.
    Mixed into CharField/TextField below so both get this behavior
    while keeping their normal Django field validation (max_length, etc).
    """

    def get_prep_value(self, value):
        # Called when Django is about to write this value to the database.
        return encrypt_value(value)

    def from_db_value(self, value, expression, connection):
        # Called when Django reads this value back out of the database.
        return decrypt_value(value)

    def to_python(self, value):
        return value

    def validate(self, value, model_instance):
        """
        Run the parent's full_clean() validators against the PLAINTEXT.

        Without this, `max_length` was checked against the *plaintext* on the
        way in (fine) but never on the way out, and — more importantly —
        `EncryptedCharField(max_length=20)` would have accepted a 300-char
        phone number with no complaint. Inheriting directly gives us the
        stock validators back.
        """
        return super().validate(value, model_instance)

    def formfield(self, **kwargs):
        # Present as a normal text input in the admin; the value is decrypted
        # before it reaches the form, so the user edits plaintext and we
        # re-encrypt on save.
        return super().formfield(**{'max_length': None, **kwargs})


class EncryptedCharField(EncryptedFieldMixin, models.CharField):
    """
    Encrypted equivalent of CharField. Note: since encrypted values are
    longer than the original plaintext (Fernet adds overhead), the actual
    database column is made wide enough regardless of the max_length you
    pass in — max_length here only validates the ORIGINAL input length,
    matching what you'd expect from a normal CharField.
    """

    def db_type(self, connection):
        # Encrypted output is significantly longer than the input,
        # so the actual column needs to be a generous text field
        # regardless of the max_length used for validation.
        return 'text'


class EncryptedTextField(EncryptedFieldMixin, models.TextField):
    """Encrypted equivalent of TextField, for longer content like message bodies."""

    pass


__all__ = [
    'EncryptedCharField',
    'EncryptedTextField',
    'encrypt_value',
    'decrypt_value',
    'ValidationError',
]
