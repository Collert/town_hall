"""Where Switchboard's events come from: signals on the things that really happen, so every
path (site, kiosk, console, API, AI connector) is covered without touching those views.

Each receiver first checks that a Switchboard is connected (one cheap query), and none may
raise: an integration must never break a sign-up or a clock-out.
"""
import logging
from functools import wraps

from django.contrib.auth.models import User
from django.db.models.signals import m2m_changed, post_save, pre_save
from django.dispatch import receiver

from base.models import PointsEntry, Profile
from education.models import TrainingModuleCompletion
from events.models import Event, EventRoleSlot
from jobs.models import Shift

from . import events

logger = logging.getLogger(__name__)


def hook(func):
    """Skip the receiver for fixtures and while nothing is connected; log instead of raising."""
    @wraps(func)
    def wrapper(sender, **kwargs):
        if kwargs.get('raw'):
            return
        try:
            if events.is_connected():
                func(sender, **kwargs)
        except Exception:
            logger.exception('Switchboard: %s failed', func.__name__)
    return wrapper


# -- volunteer.registered ----------------------------------------------------

@receiver(post_save, sender=User)
@hook
def user_registered(sender, instance, created, **kwargs):
    if created:
        events.emit('volunteer.registered', events.person_subject(instance), {
            'person': events.person_data(instance),
            'registered_at': events.iso(instance.date_joined),
        })


# -- volunteer.level_reached -------------------------------------------------
# Profile.impact_points is the ledger's sum, saved by the PointsEntry signal. The total from
# before the save is read here so the event fires only when the level really goes up.

@receiver(pre_save, sender=Profile)
@hook
def remember_points(sender, instance, update_fields=None, **kwargs):
    instance._switchboard_points_before = None
    if instance.pk and (update_fields is None or 'impact_points' in update_fields):
        instance._switchboard_points_before = (
            Profile.objects.filter(pk=instance.pk).values_list('impact_points', flat=True).first())


@receiver(post_save, sender=Profile)
@hook
def level_reached(sender, instance, created, **kwargs):
    before = getattr(instance, '_switchboard_points_before', None)
    instance._switchboard_points_before = None
    if created or before is None or instance.impact_points <= before:
        return
    ordered = events.levels()
    previous, _level = events.level_of(before, ordered)
    number, level = events.level_of(instance.impact_points, ordered)
    if number > previous:
        user = instance.user
        events.emit('volunteer.level_reached', events.person_subject(user), {
            'person': events.person_data(user),
            'level': number,
            'level_name': level.name,
            'previous_level': previous,
            'impact_points': instance.impact_points,
        })


# -- shift.signed_up / shift.cancelled ---------------------------------------

@receiver(m2m_changed, sender=EventRoleSlot.signups.through)
@hook
def signups_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ('post_add', 'post_remove') or not pk_set:
        return
    # slot.signups.add(user) or user.commitments.add(slot): either side can be the instance.
    slot_ids, user_ids = (list(pk_set), [instance.pk]) if reverse else ([instance.pk], list(pk_set))
    event_type = 'shift.signed_up' if action == 'post_add' else 'shift.cancelled'
    users = list(User.objects.filter(pk__in=user_ids))
    for slot in EventRoleSlot.objects.filter(pk__in=slot_ids).select_related('event__venue', 'role'):
        for user in users:
            events.emit(event_type, events.person_subject(user), {'person': events.person_data(user), **events.slot_data(slot)})


# -- shift.completed ---------------------------------------------------------
# A shift is completed when it gets its end time. Its points only exist once Shift.save() has
# written the ledger entry (base.points.award_shift, with this same Shift object), so the event
# goes out from there; recalculating old shifts later never sends it again.

@receiver(pre_save, sender=Shift)
@hook
def remember_open_shift(sender, instance, **kwargs):
    instance._switchboard_completing = False
    if not instance.end_time:
        return
    if not instance.pk:
        instance._switchboard_completing = True
    else:
        instance._switchboard_completing = Shift.objects.filter(pk=instance.pk, end_time__isnull=True).exists()


@receiver(post_save, sender=PointsEntry)
@hook
def shift_completed(sender, instance, created, **kwargs):
    if instance.source != PointsEntry.SHIFT or not instance.shift_id:
        return
    shift = instance.shift
    if not getattr(shift, '_switchboard_completing', False):
        return
    shift._switchboard_completing = False
    slot = shift.event_role_slot if shift.event_role_slot_id else None
    role = shift.role or (slot.role if slot else None)
    user = shift.user
    events.emit('shift.completed', events.person_subject(user), {
        'person': events.person_data(user),
        'shift': {'role': role.name if role else '', 'started_at': events.iso(shift.start_time),
                  'ended_at': events.iso(shift.end_time)},
        'hours': round(shift.duration(), 2),
        'points': instance.amount,
        'event': {'id': str(slot.event_id) if slot else '', 'title': slot.event.title if slot else ''},
    }, occurred_at=shift.end_time)


# -- event.published ---------------------------------------------------------

@receiver(pre_save, sender=Event)
@hook
def remember_published(sender, instance, **kwargs):
    instance._switchboard_was_published = bool(
        instance.pk and Event.objects.filter(pk=instance.pk, published=True).exists())


@receiver(post_save, sender=Event)
@hook
def event_published(sender, instance, **kwargs):
    was_published = getattr(instance, '_switchboard_was_published', None)
    instance._switchboard_was_published = None
    if was_published is not False or not instance.published:
        return
    from django.conf import settings
    from django.urls import reverse
    from django.utils import translation
    with translation.override(settings.LANGUAGE_CODE):
        path = reverse('opportunity_detail', args=[instance.pk])
    events.emit('event.published', {'type': 'event', 'id': str(instance.pk), 'name': instance.title}, {
        'event': {'id': str(instance.pk), 'title': instance.title, 'place': instance.place_name or '',
                  'starts_at': events.iso(instance.start_date), 'ends_at': events.iso(instance.end_date)},
    }, links={'event.url': path})


# -- training.completed ------------------------------------------------------

@receiver(post_save, sender=TrainingModuleCompletion)
@hook
def training_completed(sender, instance, created, **kwargs):
    if not created or instance.training_module.staff_only:
        return
    user, module = instance.user, instance.training_module
    events.emit('training.completed', events.person_subject(user), {
        'person': events.person_data(user),
        'module': {'id': str(module.pk), 'title': module.title},
        'completed_at': events.iso(instance.completed_at),
    })
