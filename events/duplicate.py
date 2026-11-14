"""Copy an event (console "Duplicate event") so a recurring event doesn't have to be set up again.

The copy keeps the event's details, coordinators, categories, areas and role slots, moved
so it starts at a new time. It starts unpublished and without anything that belongs to the
original run: sign-ups, shift and area leads, invites, the post-event statement, task lists,
shifts and feedback.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from modeltranslation.utils import build_localized_fieldname

from .models import Event, EventArea, EventRoleSlot


def default_start(event, now=None):
    """The original start moved on by whole weeks (at least one) until it is in the future,
    so a weekly or yearly event keeps its weekday and time."""
    now = now or timezone.now()
    start = timezone.localtime(event.start_date).replace(tzinfo=None)
    weeks = 1
    while _aware(start + timedelta(weeks=weeks)) <= now:
        weeks += 1
    return _aware(start + timedelta(weeks=weeks))


def _aware(naive):
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _shifter(old_start, new_start):
    """Move datetimes by the same wall-clock offset, so a 9 AM shift stays at 9 AM across a
    daylight saving change."""
    delta = (timezone.localtime(new_start).replace(tzinfo=None)
             - timezone.localtime(old_start).replace(tzinfo=None))

    def shift(value):
        return _aware(timezone.localtime(value).replace(tzinfo=None) + delta)
    return shift


@transaction.atomic
def duplicate_event(event, new_start):
    shift = _shifter(event.start_date, new_start)
    copy = Event.objects.get(pk=event.pk)
    copy.pk = copy.id = None
    copy._state.adding = True
    copy.start_date = shift(event.start_date)
    copy.end_date = shift(event.end_date)
    copy.published = False
    copy.leads_auto_assigned_at = None
    for code in settings.MODELTRANSLATION_LANGUAGES:
        setattr(copy, build_localized_fieldname('post_event_statement', code), None)
    copy.post_event_statement = None
    copy.save()
    copy.coordinators.set(event.coordinators.all())
    copy.category.set(event.category.all())

    areas = {}
    for area in event.areas.all():
        old_pk = area.pk
        area.pk = area.id = None
        area._state.adding = True
        area.event = copy
        area.save()
        areas[old_pk] = area

    # Area lead shifts are one person's assignment, so they aren't copied; leads are
    # assigned again on the copy.
    for slot in event.role_slots.exclude(role__system_key='area_lead').order_by('start_time', 'pk'):
        EventRoleSlot.objects.create(
            event=copy, role_id=slot.role_id, area=areas.get(slot.area_id),
            start_time=shift(slot.start_time), end_time=shift(slot.end_time),
            required_qty=slot.required_qty, allowed_overstaffing_qty=slot.allowed_overstaffing_qty,
            is_public=slot.is_public,
        )
    return copy
