from django.urls import path

from . import campaigns, views

urlpatterns = [
    path('auth/register/', views.register_user, name='register_user'),
    path('auth/login/', views.login_user, name='login_user'),

    path('profile/whatsapp-number/', views.whatsapp_profile, name='whatsapp_profile'),

    path('campaigns/', campaigns.list_campaigns, name='list_campaigns'),
    path('campaigns/create/', views.create_campaign, name='create_campaign'),
    path('campaigns/<int:campaign_id>/', campaigns.delete_campaign, name='delete_campaign'),
    path('campaigns/<int:campaign_id>/status/', views.get_campaign_status, name='campaign_status'),
    path('campaigns/<int:campaign_id>/start/', views.start_sending, name='start_sending'),
    path('campaigns/<int:campaign_id>/retry/', campaigns.retry_campaign, name='retry_campaign'),
    path(
        'campaigns/<int:campaign_id>/media/<int:file_id>/',
        views.serve_media_file,
        name='serve_media_file',
    ),

    path('login-status/', views.get_login_status, name='login_status'),
    path('whatsapp-session/reset/', views.reset_whatsapp_session, name='reset_whatsapp_session'),
]