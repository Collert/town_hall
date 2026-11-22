from django.apps import AppConfig


class SwitchboardConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'switchboard'
    verbose_name = 'Switchboard'

    def ready(self):
        from . import signals  # noqa: F401  (connects the event hooks)
