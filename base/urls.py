from django.contrib.auth import views as auth_views
from django.urls import path
from .views import *

urlpatterns = [
    path('', home, name='home'),
    path('settings/theme/', theme_settings_edit, name='theme_settings_edit'),
    path('settings/identity/', theme_settings_edit, name='identity_settings_edit'),
    path('terms/', terms_of_service, name='terms_of_service'),
    path('venues/<int:venue_id>/', venue_detail, name='venue_detail'),
    path('notifications/<int:notification_id>/read/', read_notification, name='read_notification'),
    path('notifications/read-all/', clear_notifications, name='clear_notifications'),
    path('login/', login_view, name='login'),
    path('signup/', signup_view, name='signup'),
    path('logout/', logout_view, name='logout'),
    path('password-reset/', PasswordResetView.as_view(), name='password_reset'),
    path('password-reset/sent/', auth_views.PasswordResetDoneView.as_view(template_name='base/password_reset_done.html'), name='password_reset_done'),
    path('password-reset/<uidb64>/<token>/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('password-reset/complete/', auth_views.PasswordResetCompleteView.as_view(template_name='base/password_reset_complete.html'), name='password_reset_complete'),
    path('profile/edit/', edit_profile, name='edit_profile'),
    path('profile/skills/', profile_skills, name='profile_skills'),
    path('profile/change-password/', change_password, name='change_password'),
    path('profile/deactivate/', deactivate_account, name='deactivate_account'),
    path('profile/kiosk-code/reset/', reset_kiosk_code, name='reset_kiosk_code'),
    path('profile/', my_profile, name='my_profile'),
    path('profile/<str:username>/', profile_view, name='profile_view'),
    path('impact/<str:username>/', impact_record, name='impact_record'),
    path('resume/<str:username>/', volunteer_resume, name='volunteer_resume'),
    path('endorsements/', endorsements_feed, name='endorsements_feed'),
    path('endorsements/give/', give_endorsement, name='give_endorsement'),
    path('endorsements/people/', endorse_people_search, name='endorse_people_search'),
    path('endorsements/quick/', quick_endorse, name='quick_endorse'),
]
