from django.db import migrations, models


def merge_into_multi_skill(apps, schema_editor):
    """Rows saved together by one submission (same people, event and note) become one endorsement."""
    Endorsement = apps.get_model('base', 'Endorsement')
    groups = {}
    for endorsement in Endorsement.objects.order_by('timestamp', 'pk'):
        key = (endorsement.endorser_id, endorsement.endorsed_id, endorsement.event_id, endorsement.text)
        keeper = groups.setdefault(key, endorsement)
        keeper.skills.add(endorsement.skill_id)
        if keeper.pk != endorsement.pk:
            endorsement.delete()


def split_per_skill(apps, schema_editor):
    Endorsement = apps.get_model('base', 'Endorsement')
    for endorsement in Endorsement.objects.all():
        skill_ids = list(endorsement.skills.values_list('pk', flat=True))
        if not skill_ids:
            endorsement.delete()
            continue
        endorsement.skill_id = skill_ids[0]
        endorsement.save(update_fields=['skill'])
        for skill_id in skill_ids[1:]:
            Endorsement.objects.create(endorser_id=endorsement.endorser_id, endorsed_id=endorsement.endorsed_id,
                                       skill_id=skill_id, event_id=endorsement.event_id, text=endorsement.text)


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0032_seed_venue_features'),
        ('education', '0001_initial'),
    ]

    operations = [
        migrations.AlterUniqueTogether(name='endorsement', unique_together=set()),
        migrations.AlterField(
            model_name='endorsement',
            name='skill',
            field=models.ForeignKey(null=True, on_delete=models.CASCADE, related_name='+', to='education.skill'),
        ),
        migrations.AddField(
            model_name='endorsement',
            name='skills',
            field=models.ManyToManyField(related_name='endorsements', to='education.skill'),
        ),
        migrations.RunPython(merge_into_multi_skill, split_per_skill),
        migrations.RemoveField(model_name='endorsement', name='skill'),
    ]
