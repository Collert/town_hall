# One listmonk template per email trigger and language, branding copied into listmonk,
# and each user's language so emails go out in it.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0027_emailtrigger'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmailTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('trigger', models.CharField(max_length=40)),
                ('language', models.CharField(max_length=10)),
                ('template_id', models.PositiveIntegerField()),
            ],
            options={'unique_together': {('trigger', 'language')}},
        ),
        migrations.RemoveField(model_name='emailtrigger', name='template_id'),
        migrations.AddField(
            model_name='emailtrigger',
            name='extra_list_ids',
            field=models.JSONField(blank=True, default=list, help_text='Campaign triggers: listmonk lists to send to besides "Town Hall users"'),
        ),
        migrations.RemoveField(model_name='sitesettings', name='listmonk_template_id'),
        migrations.AlterField(
            model_name='sitesettings',
            name='listmonk_list_id',
            field=models.PositiveIntegerField(blank=True, help_text='The "Town Hall users" list everyone is synced into', null=True),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='listmonk_logo_url',
            field=models.URLField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='listmonk_logo_source',
            field=models.CharField(blank=True, default='', help_text='Logo file the listmonk copy was made from', max_length=255),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='listmonk_brand',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='profile',
            name='language',
            field=models.CharField(blank=True, default='', help_text='Language the user last browsed in; emails use it', max_length=10),
        ),
    ]
