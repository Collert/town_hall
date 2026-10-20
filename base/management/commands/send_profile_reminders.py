import os

from django.core.management.base import BaseCommand, CommandError

from base.profile_completion import run_due_reminders


class Command(BaseCommand):
    help = 'Email the complete-your-profile reminders that are due (see base/profile_completion.py).'

    def add_arguments(self, parser):
        parser.add_argument('--base-url', default=os.environ.get('SITE_URL', ''),
                            help="The site's address for links in the email, e.g. https://volunteer.example.org (env: SITE_URL)")

    def handle(self, *args, **options):
        if not options['base_url']:
            raise CommandError('Pass --base-url (or set SITE_URL) so the email can link to the profile page.')
        sent = run_due_reminders(options['base_url'])
        self.stdout.write(self.style.SUCCESS(f'{sent} reminder(s) sent.'))
