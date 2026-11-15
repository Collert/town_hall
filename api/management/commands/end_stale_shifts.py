from django.core.management.base import BaseCommand

from api.shifts import end_stale_shifts


class Command(BaseCommand):
    help = 'End shifts started through the API whose heartbeats stopped more than 5 minutes ago (they end at their last heartbeat).'

    def handle(self, *args, **options):
        ended = end_stale_shifts()
        self.stdout.write(self.style.SUCCESS(f'Ended {ended} stale shift(s).'))
