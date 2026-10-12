from django.core.management.base import BaseCommand

from base.onboarding import admin_guide, ensure_admin_guide


class Command(BaseCommand):
    help = 'Add the staff-only "Town Hall Admin Guide" training module if it is missing (new installs get it at setup).'

    def handle(self, *args, **options):
        if admin_guide():
            self.stdout.write('The admin guide already exists.')
            return
        module = ensure_admin_guide()
        self.stdout.write(self.style.SUCCESS(f'Created "{module.title}" (module {module.pk}).'))
