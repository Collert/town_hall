from django.db import migrations, models


def copy_role_to_roles(apps, schema_editor):
    EventTaskList = apps.get_model('events', 'EventTaskList')
    for task_list in EventTaskList.objects.exclude(role__isnull=True):
        task_list.roles.add(task_list.role_id)


def copy_roles_to_role(apps, schema_editor):
    EventTaskList = apps.get_model('events', 'EventTaskList')
    for task_list in EventTaskList.objects.all():
        task_list.role_id = task_list.roles.values_list('pk', flat=True).first()
        task_list.save(update_fields=['role'])


class Migration(migrations.Migration):
    """A list can serve several roles once role lists are merged: role (FK) -> roles (M2M)."""

    dependencies = [
        ('events', '0042_eventtasklist_role_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='eventtasklist',
            name='roles',
            field=models.ManyToManyField(blank=True, related_name='event_task_lists_m2m', to='jobs.role'),
        ),
        migrations.RunPython(copy_role_to_roles, copy_roles_to_role),
        migrations.RemoveConstraint(model_name='eventtasklist', name='one_list_per_event_role'),
        migrations.RemoveField(model_name='eventtasklist', name='role'),
        migrations.AlterField(
            model_name='eventtasklist',
            name='roles',
            field=models.ManyToManyField(blank=True, related_name='event_task_lists', to='jobs.role'),
        ),
    ]
