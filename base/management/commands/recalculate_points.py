from django.core.management.base import BaseCommand

from base.points import rebuild


class Command(BaseCommand):
    help = 'Recalculate every finished shift and backfill training points under the current points rules.'

    def handle(self, *args, **options):
        rebuild()
        self.stdout.write(self.style.SUCCESS('Impact points recalculated.'))
