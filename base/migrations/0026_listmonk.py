# Replaces the SMTP settings with listmonk (base/listmonk.py).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0025_sitesettings_backend'),
    ]

    operations = [
        migrations.RemoveField(model_name='sitesettings', name='email_host'),
        migrations.RemoveField(model_name='sitesettings', name='email_port'),
        migrations.RemoveField(model_name='sitesettings', name='email_host_user'),
        migrations.RemoveField(model_name='sitesettings', name='email_host_password'),
        migrations.RemoveField(model_name='sitesettings', name='email_use_tls'),
        migrations.AddField(
            model_name='sitesettings',
            name='listmonk_url',
            field=models.URLField(blank=True, default='', help_text='Your listmonk server, e.g. https://lists.example.org'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='listmonk_api_user',
            field=models.CharField(blank=True, default='', help_text='API user created in listmonk (Admin > Users)', max_length=200),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='listmonk_api_token',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='listmonk_list_id',
            field=models.PositiveIntegerField(blank=True, help_text='List that volunteers are synced into', null=True),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='listmonk_template_id',
            field=models.PositiveIntegerField(blank=True, help_text='Transactional template for emails without their own template', null=True),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='listmonk_invite_template_id',
            field=models.PositiveIntegerField(blank=True, help_text='Transactional template for event invitations', null=True),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='listmonk_password_reset_template_id',
            field=models.PositiveIntegerField(blank=True, help_text='Transactional template for password resets', null=True),
        ),
        migrations.AlterField(
            model_name='sitesettings',
            name='default_from_email',
            field=models.CharField(blank=True, default='', help_text='Sender, e.g. "Town Hall <hello@example.org>". Blank uses listmonk\'s default.', max_length=200),
        ),
    ]
