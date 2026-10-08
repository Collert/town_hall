from django.db import migrations

NAMES = {'en': 'Area lead', 'es': 'Líder de área', 'fr': 'Responsable de zone', 'uk': 'Керівник зони'}
DESCRIPTIONS = {
    'en': 'Leads an area of an event: takes direction from the general coordinator and passes it on to the shift leads.',
    'es': 'Dirige un área del evento: recibe indicaciones del coordinador general y las transmite a los líderes de turno.',
    'fr': "Dirige une zone de l'événement : reçoit les consignes du coordinateur général et les transmet aux responsables d'équipe.",
    'uk': 'Керує зоною заходу: отримує вказівки від головного координатора і передає їх керівникам змін.',
}


def create_area_lead_role(apps, schema_editor):
    Role = apps.get_model('jobs', 'Role')
    if Role.objects.filter(system_key='area_lead').exists():
        return
    fields = {'name': NAMES['en'], 'description': DESCRIPTIONS['en'], 'icon': 'shield_person', 'system_key': 'area_lead'}
    for code in NAMES:
        fields[f'name_{code}'] = NAMES[code]
        fields[f'description_{code}'] = DESCRIPTIONS[code]
    Role.objects.create(**fields)


def remove_area_lead_role(apps, schema_editor):
    apps.get_model('jobs', 'Role').objects.filter(system_key='area_lead').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0014_role_system_key'),
    ]

    operations = [
        migrations.RunPython(create_area_lead_role, remove_area_lead_role),
    ]
