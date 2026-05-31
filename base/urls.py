from django.urls import path
from .views import *

urlpatterns = [
    path('', home, name='home'),
    path('settings/theme/', theme_settings_edit, name='theme_settings_edit'),
    path('settings/identity/', theme_settings_edit, name='identity_settings_edit'),
    path('<int:event_id>/download-ics/', download_ics, name='event_download_ics'),
    path('notifications/<int:notification_id>/read/', read_notification, name='read_notification'),
    path('notifications/read-all/', clear_notifications, name='clear_notifications'),
    path('login/', login_view, name='login'),
    path('signup/', signup_view, name='signup'),
    path('logout/', logout_view, name='logout'),
    path('profile/edit/', edit_profile, name='edit_profile'),
    path('profile/change-password/', change_password, name='change_password'),
    path('profile/deactivate/', deactivate_account, name='deactivate_account'),
    path('profile/', my_profile, name='my_profile'),
    path('profile/<str:username>/', profile_view, name='profile_view'),
]