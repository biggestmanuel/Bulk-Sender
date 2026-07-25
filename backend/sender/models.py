from django.db import models
from django.contrib.auth.models import User
from .encryption import EncryptedCharField, EncryptedTextField


class Campaign(models.Model):
    # Every campaign belongs to a specific user. This used to be
    # null=True/blank=True to avoid breaking pre-auth test data — that
    # transition is done now (see migration 0003), so this is required.
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='campaigns')

    name = models.CharField(max_length=255)
    message_text = EncryptedTextField()
    send_mode = models.CharField(
        max_length=10,
        choices=[('delay', 'Delay'), ('instant', 'Instant')],
        default='delay'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Contact(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name='contacts')
    name = models.CharField(max_length=255)
    phone = EncryptedCharField(max_length=20)
    status = models.CharField(
        max_length=10,
        choices=[('pending', 'Pending'), ('sent', 'Sent'), ('failed', 'Failed')],
        default='pending'
    )

    def __str__(self):
        return f"{self.name} ({self.phone})"


class MediaFile(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name='media_files')
    file = models.FileField(upload_to='campaign_media/')

    def __str__(self):
        return self.file.name


class UserProfile(models.Model):
    """
    Holds the WhatsApp phone number each user links their account with,
    used for the "log in with phone number" flow instead of QR scanning.
    Separate model (rather than extending User directly) so it can grow
    later — send preferences, etc. — without touching auth.

    whatsapp_number starts blank; a user must set it via
    GET/PUT /api/profile/whatsapp-number/ before they can start a send
    (enforced in views.start_sending).
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    whatsapp_number = EncryptedCharField(max_length=20, blank=True, default='')

    def __str__(self):
        return f"Profile for {self.user.username}"