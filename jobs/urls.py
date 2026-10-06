from django.urls import path
from . import views

urlpatterns = [
    path('kiosk/', views.kiosk_home, name='kiosk_home'),
    path('kiosk/<str:kind>/<int:place_id>/', views.kiosk_login_id_code, name='kiosk_login_id_code'),
    path('kiosk/<str:kind>/<int:place_id>/email/', views.kiosk_login_email_password, name='kiosk_login_email_password'),
    path('kiosk/<str:kind>/<int:place_id>/start/', views.kiosk_role_select, name='kiosk_start'),
    path('kiosk/shift/', views.kiosk_logged_in, name='kiosk_logged_in'),
    path('kiosk/shift/check-out/', views.kiosk_check_out, name='kiosk_check_out'),
    path('kiosk/sign-out/', views.kiosk_sign_out, name='kiosk_sign_out'),
]
