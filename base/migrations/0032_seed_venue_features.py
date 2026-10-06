from django.db import migrations

# (name, icon, category). Staff can add more from the console's venue editor.
DEFAULT_FEATURES = [
    ('Stage', 'theater_comedy', 'facilities'),
    ('Full kitchen', 'kitchen', 'facilities'),
    ('Kitchenette', 'coffee_maker', 'facilities'),
    ('On-site restrooms', 'wc', 'facilities'),
    ('Baby changing station', 'baby_changing_station', 'facilities'),
    ('Showers', 'shower', 'facilities'),
    ('Storage room', 'inventory_2', 'facilities'),
    ('Tables & chairs', 'table_restaurant', 'facilities'),
    ('Outdoor space', 'park', 'facilities'),
    ('Kids area', 'child_care', 'facilities'),
    ('First aid station', 'medical_services', 'facilities'),
    ('Air conditioning', 'ac_unit', 'facilities'),
    ('Heating', 'heat', 'facilities'),
    ('Wheelchair accessible', 'accessible', 'accessibility'),
    ('Elevator', 'elevator', 'accessibility'),
    ('Accessible restrooms', 'accessible_forward', 'accessibility'),
    ('Hearing loop', 'hearing', 'accessibility'),
    ('Quiet room', 'self_improvement', 'accessibility'),
    ('Wi-Fi', 'wifi', 'equipment'),
    ('Sound system', 'speaker', 'equipment'),
    ('Projector & screen', 'slideshow', 'equipment'),
    ('Backup power', 'bolt', 'equipment'),
    ('Loading dock', 'local_shipping', 'getting_there'),
    ('Free parking', 'local_parking', 'getting_there'),
    ('Public transit nearby', 'directions_bus', 'getting_there'),
    ('Bike racks', 'pedal_bike', 'getting_there'),
]


def seed(apps, schema_editor):
    VenueFeature = apps.get_model('base', 'VenueFeature')
    for order, (name, icon, category) in enumerate(DEFAULT_FEATURES):
        if not VenueFeature.objects.filter(name=name).exists():
            VenueFeature.objects.create(name=name, name_en=name, icon=icon, category=category, order=order)

    # Venue name/description became translatable; existing values belong to the default language.
    Venue = apps.get_model('base', 'Venue')
    for venue in Venue.objects.all():
        venue.name_en = venue.name_en or venue.name
        venue.description_en = venue.description_en or venue.description
        venue.save(update_fields=['name_en', 'description_en'])


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0031_venues'),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
