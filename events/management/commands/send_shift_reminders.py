import os

from django.core.management.base import BaseCommand, CommandError

from events.reminders import run_due_reminders


class Command(BaseCommand):
    help = 'Email the day-before shift reminders that are due (see events/reminders.py).'

    def add_arguments(self, parser):
        parser.add_argument('--base-url', default=os.environ.get('SITE_URL', ''),
                            help="The site's address for links in the email, e.g. https://volunteer.example.org (env: SITE_URL)")

    def handle(self, *args, **options):
        if not options['base_url']:
            raise CommandError('Pass --base-url (or set SITE_URL) so the email can link to the event page.')
        sent = run_due_reminders(options['base_url'])
        self.stdout.write(self.style.SUCCESS(f'{sent} reminder(s) sent.'))
