"""Fixed paths, outside the language prefix: Switchboard builds them from the manifest."""
from django.urls import path

from . import views

urlpatterns = [
    path('.well-known/switchboard', views.manifest_view, name='switchboard_manifest'),
    path('switchboard/authorize', views.authorize, name='switchboard_authorize'),
    path('switchboard/v1/token', views.token, name='switchboard_token'),
    path('switchboard/v1', views.index),
    path('switchboard/v1/', views.index, name='switchboard_index'),
    path('switchboard/v1/actions/<str:name>', views.action, name='switchboard_action'),
    path('switchboard/v1/connection', views.connection_view, name='switchboard_connection'),
]
