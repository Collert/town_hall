from django.urls import path
from . import views

urlpatterns = [
    path('', views.explore_events, name='explore_opportunities'),
    path('my-events/', views.my_events, name='my_events'),
    path('<int:event_id>/', views.event_detail, name='opportunity_detail'),
    path('slot/<int:slot_id>/signup/', views.role_slot_signup, name='role_slot_signup'),
    path('<int:event_id>/download-ics/', views.download_ics, name='event_download_ics'),
]
