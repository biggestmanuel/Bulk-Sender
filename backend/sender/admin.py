from django.contrib import admin

from .models import Campaign, Contact, DailySendQuota, MediaFile, UserProfile, WhatsAppLoginState


class ContactInline(admin.TabularInline):
    model = Contact
    # Read-only: the encrypted phone column would otherwise render as a
    # Fernet blob in an editable field, and saving it back would
    # double-encrypt the value.
    extra = 0
    fields = ['name', 'phone', 'status']
    readonly_fields = ['name', 'phone', 'status']
    can_delete = True

    def has_add_permission(self, request, obj=None):
        return False


class MediaFileInline(admin.TabularInline):
    model = MediaFile
    extra = 0
    fields = ['file']


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner', 'status', 'send_mode', 'created_at']
    list_filter = ['status', 'send_mode']
    search_fields = ['name', 'owner__username']
    # Encrypted fields are decrypted transparently on read, so editing them
    # here would work — but it's an easy way to accidentally corrupt data,
    # so the admin is read-only for the message body.
    readonly_fields = ['message_text', 'created_at', 'send_started_at', 'send_finished_at']
    inlines = [ContactInline, MediaFileInline]


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'whatsapp_number']
    search_fields = ['user__username']


@admin.register(WhatsAppLoginState)
class WhatsAppLoginStateAdmin(admin.ModelAdmin):
    list_display = ['user', 'phase', 'updated_at']
    list_filter = ['phase']
    search_fields = ['user__username']


@admin.register(DailySendQuota)
class DailySendQuotaAdmin(admin.ModelAdmin):
    list_display = ['user', 'date', 'contacts_used']
    list_filter = ['date']
    search_fields = ['user__username']
