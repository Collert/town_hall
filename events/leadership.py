"""Chain of command: areas, area leads and shift leads.

Volunteers report to their shift lead, shift leads to their area's lead, and area leads
to the event's coordinators, so questions get answered as low in the chain as possible.

- Any event can switch on ``Event.chain_of_command``; the console suggests it once the
  event needs more than ``SUGGEST_CHAIN_AT`` volunteers. Its slots are then grouped into
  ``EventArea``s. A coordinator assigns each area's leads, who are signed up to a shift
  in the built-in "Area lead" role (``Role.area_lead()``) in that area, so they check in
  and earn points like anyone else. Lead shifts should cover the area's window: its
  earliest volunteer shift start to its latest shift end.
- Every slot of a chain-of-command event has a shift lead position. Without a chain,
  only slots that more than ``SLOT_LEAD_MIN`` people can sign up for have one.
- Shift lead positions stay open until ``AUTO_ASSIGN_BEFORE`` the event starts. Then the
  open ones go to the signed-up volunteer with the most impact points (which also means
  the highest level), and the coordinators get a notification to look them over.
  ``LOCK_BEFORE`` the event starts, leads that are set can no longer change, so everyone
  knows who they report to a day ahead. Empty positions can still be filled.

``run_due_assignments`` does the 48-hour pass. A throttled middleware calls it as
requests come in, and ``manage.py assign_shift_leads`` can run it from cron.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _

SUGGEST_CHAIN_AT = 20
SLOT_LEAD_MIN = 5
AUTO_ASSIGN_BEFORE = timedelta(hours=48)
LOCK_BEFORE = timedelta(hours=24)
CHECK_EVERY = 300  # seconds between middleware passes

OPEN, REVIEW, LOCKED = 'open', 'review', 'locked'


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def capacity(slot):
    return slot.required_qty + slot.allowed_overstaffing_qty


def has_lead_position(slot, event=None):
    if slot.is_area_lead:  # area leads lead the area, not a team of their own
        return False
    event = event or slot.event
    return event.chain_of_command or capacity(slot) > SLOT_LEAD_MIN


def volunteers_needed(event):
    return event.total_required()


def suggests_chain(event):
    return not event.chain_of_command and volunteers_needed(event) > SUGGEST_CHAIN_AT


def phase(event, now=None):
    """OPEN until 48 hours before the event, REVIEW for the next day, then LOCKED."""
    now = now or timezone.now()
    if now < event.start_date - AUTO_ASSIGN_BEFORE:
        return OPEN
    if now < event.start_date - LOCK_BEFORE:
        return REVIEW
    return LOCKED


def auto_assign_at(event):
    return event.start_date - AUTO_ASSIGN_BEFORE


def lock_at(event):
    return event.start_date - LOCK_BEFORE


def can_change_lead(event, current_lead_id, now=None):
    """Leads lock a day before the event, except positions nobody holds yet."""
    return current_lead_id is None or phase(event, now) != LOCKED


def can_manage_leads(user, event):
    """Leads are assigned by the event's general coordinators (any staff member while the
    event has none, and superusers)."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    coordinator_ids = set(event.coordinators.values_list('pk', flat=True))
    return user.pk in coordinator_ids if coordinator_ids else user.is_staff


# ---------------------------------------------------------------------------
# Shift leads
# ---------------------------------------------------------------------------

def ranked_signups(slot):
    """The slot's volunteers, best lead candidate first: most impact points, then whoever
    signed up first."""
    from .models import SlotSignup
    joined = dict(SlotSignup.objects.filter(slot=slot).values_list('user_id', 'created_at'))
    later = timezone.now() + timedelta(days=36500)
    people = list(slot.signups.select_related('profile'))
    return sorted(people, key=lambda u: (-u.profile.impact_points, joined.get(u.pk, later), u.pk))


def suggested_lead(slot):
    return next(iter(ranked_signups(slot)), None)


def set_slot_lead(slot, user, auto=False, notify_user=True):
    """Make ``user`` (one of the slot's volunteers, or None) the slot's shift lead."""
    old_id = slot.lead_id
    slot.lead = user
    slot.lead_auto_assigned = bool(user and auto)
    slot.save(update_fields=['lead', 'lead_auto_assigned'])
    if user and user.pk != old_id and notify_user:
        notify_person(user, _('You are the shift lead for %(role)s at %(event)s.'),
                {'role': slot.role.name, 'event': slot.event.title},
                reverse('opportunity_detail', args=[slot.event_id]))
    if (user.pk if user else None) != old_id:
        from base import belltower
        if belltower.is_connected():
            from . import belltower_sync
            belltower.run_after_commit(belltower_sync.slot_lead_changed, slot.pk, old_id)


def run_due_assignments(now=None):
    """Fill open shift lead positions of events starting within 48 hours, and tell each
    event's coordinators the first time it happens. Returns how many leads were picked."""
    from .models import Event, EventRoleSlot
    now = now or timezone.now()
    events = Event.objects.filter(start_date__lte=now + AUTO_ASSIGN_BEFORE, end_date__gt=now)
    picked = 0
    for event in events:
        slots = (EventRoleSlot.objects.filter(event=event, lead__isnull=True).exclude(role__system_key__isnull=False)
                 .annotate(n=Count('signups')).filter(n__gt=0).select_related('role', 'event'))
        event_picked = 0
        for slot in slots:
            if not has_lead_position(slot, event):
                continue
            user = suggested_lead(slot)
            if user:
                set_slot_lead(slot, user, auto=True)
                event_picked += 1
        picked += event_picked
        if event_picked and event.leads_auto_assigned_at is None:
            Event.objects.filter(pk=event.pk).update(leads_auto_assigned_at=now)
            link = reverse('console_event_roles', args=[event.pk])
            for coordinator in event.coordinators.all():
                notify_person(coordinator, _('Shift leads for %(event)s were picked automatically. Look them over before they lock 24 hours ahead.'),
                        {'event': event.title}, link)
    return picked


def run_due_if_idle():
    """Run the 48-hour pass at most every CHECK_EVERY seconds (called by middleware)."""
    if cache.add('leadership_due_check', True, CHECK_EVERY):
        try:
            run_due_assignments()
        except Exception as exc:  # never break a page over this
            print(f'[leadership] auto-assignment failed: {exc}')


def drop_lead_if_gone(slot_ids, user_ids):
    """People left these slots: a slot they led has no lead any more."""
    from .models import EventRoleSlot
    for slot in EventRoleSlot.objects.filter(pk__in=slot_ids, lead_id__in=user_ids):
        set_slot_lead(slot, None)


def notify_person(user, message, params, link):
    from base.models import Notification
    lang = getattr(getattr(user, 'profile', None), 'language', '') or None
    with translation.override(lang or translation.get_language()):
        text = str(message) % params
    Notification.objects.create(user=user, message=text[:255], link=link)


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------

def gaps(start, end, intervals):
    """The parts of [start, end) that no (start, end) interval covers."""
    result, cursor = [], start
    for lo, hi in sorted(intervals):
        if hi <= cursor:
            continue
        if lo > cursor:
            result.append((cursor, min(lo, end)))
        cursor = max(cursor, hi)
        if cursor >= end:
            break
    if cursor < end:
        result.append((cursor, end))
    return [(lo, hi) for lo, hi in result if hi > lo]


def area_coverage(area, leads=None):
    """{'start', 'end', 'gaps'}: the area's window and the stretches no lead covers."""
    start, end = area.window()
    leads = area.lead_slots() if leads is None else leads
    return {'start': start, 'end': end, 'gaps': gaps(start, end, [(l.start_time, l.end_time) for l in leads])}


def assign_area_lead(area, user, start, end, lead_slot=None):
    """Sign ``user`` up to lead ``area`` from ``start`` to ``end``: a new shift in the
    "Area lead" role, or ``lead_slot`` changed (another person and/or other hours).
    Sign-up signals keep Bell Tower in step (area leads are admins of the area's lists)."""
    from jobs.models import Role
    from .models import EventRoleSlot, SlotSignup
    if lead_slot is None:
        lead_slot = EventRoleSlot(event=area.event, role=Role.area_lead(), area=area,
                                  required_qty=1, allowed_overstaffing_qty=0, is_public=False)
    lead_slot.start_time, lead_slot.end_time = start, end
    lead_slot.save()
    current = list(lead_slot.signups.all())
    if [p.pk for p in current] == [user.pk]:
        return lead_slot
    for person in current:
        lead_slot.signups.remove(person)
        SlotSignup.objects.filter(slot=lead_slot, user=person).delete()
    lead_slot.add_signup(user)
    notify_person(user, _('You are the area lead for %(area)s at %(event)s.'),
                  {'area': area.name, 'event': area.event.title}, reverse('opportunity_detail', args=[area.event_id]))
    return lead_slot


def remove_area_lead(lead_slot):
    """Take the lead off (so Bell Tower demotes them) and delete their shift."""
    people = list(lead_slot.signups.all())
    if people:
        lead_slot.signups.remove(*people)
    area_id = lead_slot.area_id
    lead_slot.delete()
    # The sign-up signal's job can't find the deleted shift, so update Bell Tower from the area.
    from base import belltower
    if people and area_id and belltower.is_connected():
        from . import belltower_sync
        belltower.run_after_commit(belltower_sync.area_leads_changed, area_id, (), [p.pk for p in people])


def remove_area_leads(areas):
    for area in areas:
        for lead_slot in area.lead_slots():
            remove_area_lead(lead_slot)


def area_lead_candidates(event, query='', limit=8):
    """Active people to pick an area lead from, best first: the event's own volunteers and
    coordinators, then by impact points."""
    User = get_user_model()
    people = User.objects.filter(is_active=True).select_related('profile')
    if query:
        people = people.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query)
                               | Q(username__icontains=query) | Q(email__icontains=query))
    involved = set(event.volunteers().values_list('pk', flat=True)) | set(event.coordinators.values_list('pk', flat=True))
    people = sorted(people.order_by('-profile__impact_points', 'first_name')[:200],
                    key=lambda u: (u.pk not in involved,))
    return people[:limit]


def _overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def area_leads_for_slot(slot, leads=None):
    """The area lead shifts on duty during ``slot`` (most overlap first). Each has
    ``.person``, the lead."""
    if not slot.area_id or slot.is_area_lead:
        return []
    leads = list(slot.area.lead_slots()) if leads is None else leads
    on_duty = [l for l in leads if l.person and _overlaps(l.start_time, l.end_time, slot.start_time, slot.end_time)]
    overlap = lambda l: min(l.end_time, slot.end_time) - max(l.start_time, slot.start_time)
    return sorted(on_duty, key=overlap, reverse=True)


def my_area_leads(event, user):
    """The area lead shifts of whoever leads ``user``'s areas during their shifts, one per
    person (empty without a chain)."""
    if not (event.chain_of_command and user.is_authenticated):
        return []
    from .models import EventRoleSlot
    people = {}
    slots = (EventRoleSlot.objects.filter(event=event, signups=user, area__isnull=False)
             .exclude(role__system_key__isnull=False).select_related('area', 'role'))
    for slot in slots:
        for lead in area_leads_for_slot(slot):
            if lead.person.pk != user.pk:
                people.setdefault(lead.person.pk, lead)
    return list(people.values())


def area_team(lead_slot):
    """For an area lead's own shift: the area's shift leads on duty with them."""
    from .models import EventRoleSlot
    slots = (EventRoleSlot.objects.filter(area_id=lead_slot.area_id, lead__isnull=False)
             .exclude(role__system_key__isnull=False).select_related('lead__profile', 'role').order_by('start_time'))
    return [s for s in slots if _overlaps(s.start_time, s.end_time, lead_slot.start_time, lead_slot.end_time)]
