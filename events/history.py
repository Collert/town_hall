"""A volunteer's finished event shifts, grouped the way they're shown to people.

Clocking out and back in within the same role slot keeps separate ``Shift`` rows, but
they're shown as one shift: its hours are the time actually worked (breaks don't count)
and its points are the sum. ``worked_events`` then rolls those up per event.
"""
from jobs.models import Shift


def finished_shifts(user, **filters):
    return (Shift.objects
            .filter(user=user, event_role_slot__isnull=False, end_time__isnull=False, **filters)
            .select_related('event_role_slot__event__venue', 'role'))


def worked_slots(shifts):
    """One dict per role slot (slot, event, role, start, end, hours, points), newest first."""
    groups = {}
    for shift in shifts:
        group = groups.setdefault(shift.event_role_slot_id, {
            'slot': shift.event_role_slot,
            'event': shift.event_role_slot.event,
            'role': shift.role or shift.event_role_slot.role,
            'start': shift.start_time,
            'end': shift.end_time,
            'hours': 0,
            'points': 0,
        })
        group['start'] = min(group['start'], shift.start_time)
        group['end'] = max(group['end'], shift.end_time)
        group['hours'] += shift.duration()
        group['points'] += shift.end_impact_points or 0
    return sorted(groups.values(), key=lambda g: g['start'], reverse=True)


def worked_events(shifts):
    """One dict per event (event, slots, roles, start, hours, points), newest first.
    ``roles`` lists each role once, so two slots of the same role are one job."""
    events = {}
    for item in reversed(worked_slots(shifts)):  # oldest first within an event
        entry = events.setdefault(item['event'].pk, {
            'event': item['event'], 'slots': [], 'roles': [], 'start': item['start'], 'hours': 0, 'points': 0,
        })
        entry['slots'].append(item)
        if item['role'] not in entry['roles']:
            entry['roles'].append(item['role'])
        entry['hours'] += item['hours']
        entry['points'] += item['points']
    return sorted(events.values(), key=lambda e: e['start'], reverse=True)
