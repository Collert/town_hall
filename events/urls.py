from django.urls import path
from . import views

urlpatterns = [
    path('', views.explore_events, name='explore_opportunities'),
    path('my-events/', views.my_events, name='my_events'),
    path('<int:event_id>/', views.event_detail, name='opportunity_detail'),
    path('<int:event_id>/thank-you/', views.event_thank_you, name='event_thank_you'),
    path('<int:event_id>/certificate/<str:username>/', views.event_certificate, name='event_certificate'),
    path('<int:event_id>/impact-card/', views.event_impact_card, name='event_impact_card'),
    path('slot/<int:slot_id>/signup/', views.role_slot_signup, name='role_slot_signup'),
    path('invite/<str:token>/', views.respond_to_invite, name='respond_to_invite'),
    path('<int:event_id>/download-ics/', views.download_ics, name='event_download_ics'),
    path('search/', views.search_events, name='search_events'),
]
