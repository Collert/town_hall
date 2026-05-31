from django.urls import path
from .views import *

urlpatterns = [
    path('', training_directory, name='training_directory'),
    path('dashboard/', training_dashboard, name='training_dashboard'),
    path('module/<int:module_id>/', module_overview, name='module_overview'),
    path('module/<int:module_id>/lesson/<int:lesson_id>/', lesson_detail, name='lesson_detail'),
    path('module/<int:module_id>/lesson/<int:lesson_id>/complete/', mark_lesson_complete, name='mark_lesson_complete'),
    path('module/<int:module_id>/quiz/<int:quiz_id>/', quiz_detail, name='quiz_detail'),
    path('module/<int:module_id>/quiz/<int:quiz_id>/submit/', submit_quiz, name='submit_quiz'),
    path('module/<int:module_id>/quiz/<int:quiz_id>/results/', quiz_results, name='quiz_results'),
    path('module/<int:module_id>/complete/', complete_module, name='complete_module'),
    path('certificate/<int:completion_id>/', completed_module, name='completed_module'),
    path('certificate/<int:completion_id>/print/', printable_certificate, name='printable_certificate'),
    path('verify/', verify_certificates, name='verify_certificates'),
    path('verify/<int:cert_id>/submit/', submit_certificate, name='submit_certificate'),
]