from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0030_alter_eventslotinvite_token'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(
                    name='Shift',
                ),
            ],
            database_operations=[],
        ),
    ]
