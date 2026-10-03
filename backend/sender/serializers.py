from rest_framework import serializers

from .models import Campaign, Contact, MediaFile, UserProfile, WhatsAppLoginState


class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = ['id', 'name', 'phone', 'status']


class MediaFileSerializer(serializers.ModelSerializer):
    # Not exposed as a bare .file URL: that path is no longer publicly served
    # (see sender.views.serve_media_file). The client builds the authenticated
    # URL from campaign_id + id instead.
    id = serializers.IntegerField(read_only=True)
    name = serializers.SerializerMethodField()
    size = serializers.SerializerMethodField()

    class Meta:
        model = MediaFile
        fields = ['id', 'name', 'size']

    def get_name(self, obj):
        return obj.file.name.rsplit('/', 1)[-1]

    def get_size(self, obj):
        try:
            return obj.file.size
        except Exception:
            return None


class CampaignSerializer(serializers.ModelSerializer):
    """Full detail, including every contact. Used by the status endpoint."""

    contacts = ContactSerializer(many=True, read_only=True)
    media_files = MediaFileSerializer(many=True, read_only=True)

    class Meta:
        model = Campaign
        fields = [
            'id',
            'name',
            'message_text',
            'send_mode',
            'status',
            'status_detail',
            'created_at',
            'send_started_at',
            'send_finished_at',
            'last_heartbeat_at',
            'contacts',
            'media_files',
        ]
        # Only the campaign's owner should ever see it, and the view already
        # scopes every lookup to request.user. Read-only here means a crafted
        # POST can't try to set owner or status directly.
        read_only_fields = fields


class CampaignListSerializer(serializers.ModelSerializer):
    """
    Summary row for the campaign list.

    No contacts and no message body: the list only needs enough to identify a
    campaign and show its progress. Counts are annotated in the queryset, so
    this costs the same regardless of campaign size.
    """

    total_contacts = serializers.IntegerField(read_only=True)
    sent_contacts = serializers.IntegerField(read_only=True)
    failed_contacts = serializers.IntegerField(read_only=True)
    pending_contacts = serializers.SerializerMethodField()
    media_count = serializers.SerializerMethodField()
    can_retry = serializers.SerializerMethodField()

    class Meta:
        model = Campaign
        fields = [
            'id',
            'name',
            'send_mode',
            'status',
            'status_detail',
            'created_at',
            'send_started_at',
            'send_finished_at',
            'total_contacts',
            'sent_contacts',
            'failed_contacts',
            'pending_contacts',
            'media_count',
            'can_retry',
        ]
        read_only_fields = fields

    def get_pending_contacts(self, obj):
        total = getattr(obj, 'total_contacts', None) or 0
        sent = getattr(obj, 'sent_contacts', None) or 0
        failed = getattr(obj, 'failed_contacts', None) or 0
        return max(0, total - sent - failed)

    def get_media_count(self, obj):
        # Prefetched by the queryset when present, otherwise counted.
        prefetched = getattr(obj, 'media_files', None)
        if prefetched is not None and hasattr(prefetched, 'all'):
            return len(prefetched.all())
        return obj.media_files.count()

    def get_can_retry(self, obj):
        """Only offer retry once nothing is in flight and something failed."""
        if obj.status in Campaign.IN_FLIGHT_STATUSES:
            return False
        return (getattr(obj, 'failed_contacts', None) or 0) > 0


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = ['whatsapp_number']


class WhatsAppLoginStateSerializer(serializers.ModelSerializer):
    class Meta:
        model = WhatsAppLoginState
        fields = ['phase', 'login_code', 'error', 'updated_at']
        read_only_fields = fields