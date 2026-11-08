# Keys are now stored as hashes and scoped to roles, and each API-started shift has one
# heartbeat. The old tables were never reachable (no routes), so they're rebuilt from scratch.
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0003_alter_apikey_key'),
        ('jobs', '0017_role_certificate_requirement'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.DeleteModel(name='ShiftHeartbeat'),
        migrations.DeleteModel(name='APIKey'),
        migrations.CreateModel(
            name='APIKey',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Which application uses this key, e.g. "Library admin site".', max_length=100, verbose_name='name')),
                ('prefix', models.CharField(editable=False, help_text='The start of the key, shown so staff can tell keys apart', max_length=16)),
                ('key_hash', models.CharField(editable=False, max_length=64, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('last_used_at', models.DateTimeField(blank=True, null=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('roles', models.ManyToManyField(blank=True, help_text='Permanent roles this key may start shifts in. Empty means every permanent role.', related_name='api_keys', to='jobs.role')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='ShiftHeartbeat',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('last_seen', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('api_key', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='heartbeats', to='api.apikey')),
                ('shift', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='heartbeat', to='jobs.shift')),
            ],
        ),
    ]
