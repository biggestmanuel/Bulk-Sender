from django.db import models
from django.contrib.auth.models import User
from .encryption import EncryptedCharField, EncryptedTextField


class Campaign(models.Model):
    # Every campaign now belongs to a specific user — this is what makes
    # per-user isolation possible. null=True/blank=True temporarily so
    # existing local test data doesn't break; tighten this once real
    # accounts are in use everywhere.
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='campaigns', null=True, blank=True)

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