from django.db import models

class Campaign(models.Model):
    name = models.CharField(max_length=255)
    message_text = models.TextField()
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
    phone = models.CharField(max_length=20)
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