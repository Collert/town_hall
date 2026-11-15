"""Routed outside the language prefix at /api/v1/ (town_hall/urls.py), so clients can rely on fixed paths."""
from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='api_index'),
    path('shifts/start', views.shift_start, name='api_shift_start'),
    path('shifts/heartbeat', views.shift_heartbeat, name='api_shift_heartbeat'),
    path('shifts/end', views.shift_end, name='api_shift_end'),
    path('shifts/status', views.shift_status, name='api_shift_status'),
]
