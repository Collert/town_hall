from django.core.management.base import BaseCommand

from switchboard import delivery


class Command(BaseCommand):
    help = 'Send Switchboard event deliveries that are due for a retry, and prune the delivery log.'

    def handle(self, *args, **options):
        tried = delivery.run_due()
        delivery.prune()
        self.stdout.write(f'Tried {tried} deliveries.')
