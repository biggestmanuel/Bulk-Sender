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
"""

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


def _get_fernet():
    """
    Builds the Fernet cipher from CRYPTOGRAPHY_KEY in settings.py.
    That key must be a valid Fernet key (44-character base64 string) —
    the one generated via:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    """
    key = settings.CRYPTOGRAPHY_KEY
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


class EncryptedFieldMixin:
    """
    Shared logic for encrypting on save and decrypting on read.
    Mixed into CharField/TextField below so both get this behavior
    while keeping their normal Django field validation (max_length, etc).
    """

    def get_prep_value(self, value):
        # Called when Django is about to write this value to the database.
        if value is None or value == '':
            return value
        f = _get_fernet()
        encrypted = f.encrypt(str(value).encode())
        return encrypted.decode()  # store as text in the DB column

    def from_db_value(self, value, expression, connection):
        # Called when Django reads this value back out of the database.
        if value is None or value == '':
            return value
        f = _get_fernet()
        try:
            decrypted = f.decrypt(value.encode())
            return decrypted.decode()
        except InvalidToken:
            # Value wasn't encrypted with the current key (e.g. old
            # unencrypted test data, or a key mismatch). Fail loudly
            # rather than silently returning garbage.
            raise ValueError(
                "Could not decrypt field value — CRYPTOGRAPHY_KEY may have "
                "changed, or this data was saved before encryption was added."
            )

    def to_python(self, value):
        return value


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
