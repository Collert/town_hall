from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0006_add_shift_from_events_table'),
    ]

    operations = [
        migrations.AlterModelTable(
            name='shift',
            table=None,
        ),
    ]
