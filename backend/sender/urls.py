from django.urls import path
from . import views

urlpatterns = [
    path('campaigns/create/', views.create_campaign, name='create_campaign'),
    path('campaigns/<int:campaign_id>/status/', views.get_campaign_status, name='campaign_status'),
    path('campaigns/<int:campaign_id>/start/', views.start_sending, name='start_sending'),
]