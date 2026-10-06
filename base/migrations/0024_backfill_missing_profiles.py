import secrets
import string

from django.conf import settings
from django.db import migrations


def create_missing_profiles(apps, schema_editor):
    """Users created before the post_save signal (or whose profile was deleted) have no Profile."""
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    Profile = apps.get_model('base', 'Profile')
    used = set(Profile.objects.values_list('id_code', flat=True))
    for user in User.objects.filter(profile__isnull=True):
        while True:
            code = ''.join(secrets.choice(string.digits) for _ in range(6))
            if code not in used:
                used.add(code)
                break
        Profile.objects.create(user=user, id_code=code)


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0023_endorsement_event_profile_admin_notes_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(create_missing_profiles, migrations.RunPython.noop),
    ]
