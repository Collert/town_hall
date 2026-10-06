from django.db import migrations


def link(apps, schema_editor):
    """Point events whose typed location matches an existing venue's name or address at that venue."""
    Event = apps.get_model('events', 'Event')
    Venue = apps.get_model('base', 'Venue')
    lookup = {}
    for venue in Venue.objects.all():
        for value in (venue.name_en or venue.name, venue.address):
            if value:
                lookup.setdefault(value.strip().lower(), venue)
    for event in Event.objects.filter(venue__isnull=True):
        venue = lookup.get((event.location_en or event.location or '').strip().lower())
        if venue:
            event.venue = venue
            event.latitude, event.longitude = venue.latitude, venue.longitude
            event.save(update_fields=['venue', 'latitude', 'longitude'])


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0032_seed_venue_features'),
        ('events', '0038_venues'),
    ]

    operations = [
        migrations.RunPython(link, migrations.RunPython.noop),
    ]
