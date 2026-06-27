from rest_framework import serializers
from .models import Campaign, Contact, MediaFile


class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = ['id', 'name', 'phone', 'status']


class MediaFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = MediaFile
        fields = ['id', 'file']


class CampaignSerializer(serializers.ModelSerializer):
    contacts = ContactSerializer(many=True, read_only=True)
    media_files = MediaFileSerializer(many=True, read_only=True)

    class Meta:
        model = Campaign
        fields = ['id', 'name', 'message_text', 'send_mode', 'created_at', 'contacts', 'media_files']