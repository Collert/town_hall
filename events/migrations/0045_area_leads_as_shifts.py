"""Area leads become shifts in the built-in "Area lead" role instead of their own model."""
from django.db import migrations


def leads_to_slots(apps, schema_editor):
    AreaLead = apps.get_model('events', 'AreaLead')
    EventRoleSlot = apps.get_model('events', 'EventRoleSlot')
    SlotSignup = apps.get_model('events', 'SlotSignup')
    Role = apps.get_model('jobs', 'Role')
    role = Role.objects.filter(system_key='area_lead').first()
    for lead in AreaLead.objects.select_related('area'):
        slot = EventRoleSlot.objects.create(
            event_id=lead.area.event_id, role=role, area=lead.area, start_time=lead.start_time,
            end_time=lead.end_time, required_qty=1, allowed_overstaffing_qty=0, is_public=False,
        )
        slot.signups.add(lead.user_id)
        SlotSignup.objects.get_or_create(slot=slot, user_id=lead.user_id)


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0044_chain_of_command'),
        ('jobs', '0015_area_lead_role'),
    ]

    operations = [
        migrations.RunPython(leads_to_slots, migrations.RunPython.noop),
        migrations.DeleteModel(name='AreaLead'),
    ]
