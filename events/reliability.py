"""Reliability: how often a volunteer turns up for the shifts they sign up for.

Everyone starts at 100%. Two things count against it:

- a no-show: the slot has ended, the volunteer is still signed up for it, and they either
  never checked in (kiosk or the console's check-in monitor) or checked in after more than
  half of the slot had passed. Arriving late but within the first half is fine;
- a late cancellation: taking themselves off a slot less than ``LATE_CANCEL_WINDOW``
  before it starts (``SlotCancellation.late``).

The score is the share of those commitments that were kept: kept / (kept + misses).
Cancelling earlier than that doesn't count either way. Staff taking someone off a slot
(area lead changes) records no cancellation.
"""
from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import Min
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

LATE_CANCEL_WINDOW = timedelta(hours=24)


def is_late(slot, now=None):
    return slot.start_time - (now or timezone.now()) < LATE_CANCEL_WINDOW


def showed_up(slot, checked_in_at):
    """Checked in before half of the slot had gone by (None: never checked in)."""
    return checked_in_at is not None and checked_in_at <= slot.start_time + (slot.end_time - slot.start_time) / 2


def cancellable(slots, user, now=None):
    """The slots in ``slots`` that ``user`` can take themselves off: not over, not checked
    in yet, and not an area lead shift (coordinators assign those). Each gets ``.late``."""
    from jobs.models import Shift
    now = now or timezone.now()
    slots = [s for s in slots if not s.is_area_lead and s.end_time > now]
    started = set(Shift.objects.filter(user=user, event_role_slot__in=[s.pk for s in slots])
                  .values_list('event_role_slot_id', flat=True))
    result = [s for s in slots if s.pk not in started]
    for slot in result:
        slot.late = is_late(slot, now)
    return result


def cancel_signup(slot, user, now=None):
    """Take ``user`` off ``slot`` and record it. Sign-up signals drop them as the slot's
    shift lead and from its Bell Tower list. Returns the ``SlotCancellation``."""
    from .leadership import notify_person
    from .models import EventSlotInvite, SlotCancellation, SlotSignup
    late = is_late(slot, now)
    slot.signups.remove(user)
    SlotSignup.objects.filter(slot=slot, user=user).delete()
    # An accepted invite to an invite-only slot opens again, so they can come back.
    EventSlotInvite.objects.filter(event_role_slot=slot, user=user, accepted=True).update(accepted=False)
    cancellation = SlotCancellation.objects.create(slot=slot, user=user, late=late)
    if late:
        name = user.get_full_name() or user.username
        link = reverse('console_event_roles', args=[slot.event_id])
        for coordinator in slot.event.coordinators.exclude(pk=user.pk):
            notify_person(coordinator, _('%(name)s cancelled their %(role)s shift at %(event)s less than 24 hours before it starts.'),
                          {'name': name, 'role': slot.role.name, 'event': slot.event.title}, link)
    return cancellation


@dataclass
class Reliability:
    score: int = 100
    kept: int = 0
    no_shows: list = field(default_factory=list)  # EventRoleSlots
    late_cancellations: list = field(default_factory=list)  # SlotCancellations

    @property
    def misses(self):
        return len(self.no_shows) + len(self.late_cancellations)

    @property
    def total(self):
        return self.kept + self.misses

    def recent_misses(self, limit=5):
        """[{'kind', 'slot', 'when'}] newest first, for the console."""
        rows = [{'kind': 'no_show', 'slot': s, 'when': s.start_time} for s in self.no_shows]
        rows += [{'kind': 'late_cancel', 'slot': c.slot, 'when': c.created_at} for c in self.late_cancellations]
        return sorted(rows, key=lambda r: r['when'], reverse=True)[:limit]


def for_user(user, now=None):
    from jobs.models import Shift
    from .models import EventRoleSlot, SlotCancellation
    now = now or timezone.now()
    past = list(EventRoleSlot.objects.filter(signups=user, end_time__lt=now).select_related('event', 'role'))
    arrived = dict(Shift.objects.filter(user=user, event_role_slot__in=[s.pk for s in past])
                   .values('event_role_slot_id').annotate(first=Min('start_time'))
                   .values_list('event_role_slot_id', 'first'))
    no_shows = [s for s in past if not showed_up(s, arrived.get(s.pk))]
    late = list(SlotCancellation.objects.filter(user=user, late=True).select_related('slot__event', 'slot__role'))
    result = Reliability(kept=len(past) - len(no_shows), no_shows=no_shows, late_cancellations=late)
    if result.total:
        result.score = round(result.kept / result.total * 100)
    return result
