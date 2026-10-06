# Migration 0020 gave some profiles kiosk codes with letters (from a UUID). The kiosk keypad
# only has digits, so those volunteers could never check in; give them 6-digit codes.

import secrets
import string

from django.db import migrations


def renumber_codes(apps, schema_editor):
    Profile = apps.get_model('base', 'Profile')
    used = set(Profile.objects.values_list('id_code', flat=True))
    for profile in Profile.objects.all():
        if profile.id_code.isdigit() and len(profile.id_code) == 6:
            continue
        code = profile.id_code
        while code in used:
            code = ''.join(secrets.choice(string.digits) for _ in range(6))
        used.add(code)
        profile.id_code = code
        profile.save(update_fields=['id_code'])


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0028_email_templates_per_language'),
    ]

    operations = [
        migrations.RunPython(renumber_codes, migrations.RunPython.noop),
    ]
