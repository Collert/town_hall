from django.apps import AppConfig


class ConsoleConfig(AppConfig):
    """Staff-facing admin portal: events, roles, volunteers, training and settings."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'console'
