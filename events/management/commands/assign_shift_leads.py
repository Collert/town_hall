from django.core.management.base import BaseCommand

from events.leadership import run_due_assignments


class Command(BaseCommand):
    help = 'Fill open shift lead positions of events starting within 48 hours (see events/leadership.py).'

    def handle(self, *args, **options):
        picked = run_due_assignments()
        self.stdout.write(self.style.SUCCESS(f'{picked} shift lead(s) assigned.'))
