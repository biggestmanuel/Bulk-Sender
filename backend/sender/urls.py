from django.urls import path
from . import views

urlpatterns = [
    path('auth/register/', views.register_user, name='register_user'),
    path('auth/login/', views.login_user, name='login_user'),

    path('profile/whatsapp-number/', views.whatsapp_profile, name='whatsapp_profile'),

    path('campaigns/create/', views.create_campaign, name='create_campaign'),
    path('campaigns/<int:campaign_id>/status/', views.get_campaign_status, name='campaign_status'),
    path('campaigns/<int:campaign_id>/start/', views.start_sending, name='start_sending'),
    path('login-status/', views.get_login_status, name='login_status'),
]