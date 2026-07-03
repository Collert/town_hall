from django.conf import settings
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0031_remove_shift_from_events_state'),
        ('jobs', '0005_role_regular_number_of_beneficiaries'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='Shift',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('start_time', models.DateTimeField(auto_now_add=True)),
                        ('end_time', models.DateTimeField(blank=True, null=True)),
                        ('event_role_slot', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='shifts', to='events.eventroleslot')),
                        ('role', models.ForeignKey(blank=True, help_text='Optional role reference for shifts that are not tied to a specific event role slot', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='shifts', to='jobs.role')),
                        ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='shifts', to=settings.AUTH_USER_MODEL)),
                    ],
                    options={
                        'db_table': 'events_shift',
                    },
                ),
            ],
            database_operations=[],
        ),
    ]
