from django.core.management.base import BaseCommand

from switchboard import delivery, timed


class Command(BaseCommand):
    help = ('Emit the Switchboard events that have come due with time (an event starting, a missed shift, ...), '
            'send event deliveries that are due for a retry, and prune the delivery log.')

    def handle(self, *args, **options):
        emitted = timed.run_due()
        tried = delivery.run_due()
        delivery.prune()
        self.stdout.write(f'Emitted {emitted} time-based events. Tried {tried} deliveries.')
