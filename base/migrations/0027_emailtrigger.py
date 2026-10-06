# Per-event email settings replace the fixed invitation / password reset template fields.

from django.db import migrations, models

MOVED = {'invitation': 'listmonk_invite_template_id', 'password_reset': 'listmonk_password_reset_template_id'}


def copy_template_ids(apps, schema_editor):
    SiteSettings = apps.get_model('base', 'SiteSettings')
    EmailTrigger = apps.get_model('base', 'EmailTrigger')
    site = SiteSettings.objects.filter(pk=1).first()
    if not site:
        return
    for key, field in MOVED.items():
        template_id = getattr(site, field)
        if template_id:
            EmailTrigger.objects.update_or_create(key=key, defaults={'enabled': True, 'template_id': template_id})


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0026_listmonk'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmailTrigger',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=40, unique=True)),
                ('enabled', models.BooleanField(default=False)),
                ('template_id', models.PositiveIntegerField(blank=True, help_text='listmonk template; blank uses the default template', null=True)),
                ('auto_send', models.BooleanField(default=False, help_text='Campaign triggers: start the campaign instead of leaving a draft')),
            ],
        ),
        migrations.RunPython(copy_template_ids, migrations.RunPython.noop),
        migrations.RemoveField(model_name='sitesettings', name='listmonk_invite_template_id'),
        migrations.RemoveField(model_name='sitesettings', name='listmonk_password_reset_template_id'),
    ]
