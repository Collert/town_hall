"""Where Switchboard's events come from: signals on the things that really happen, so every
path (site, kiosk, console, API, AI connector, Switchboard's own actions) is covered without
touching those views. The one exception is `invite.declined`, which only its view can tell
apart (events.invite_declined). Events that depend on the clock are in switchboard/timed.py.

Each receiver first checks that a Switchboard is connected (one cheap query), and none may
raise: an integration must never break a sign-up or a clock-out. A `pre_save` receiver that
needs the row as it was reads only the columns it compares.
"""
import logging
from functools import wraps

from django.contrib.auth.models import User
from django.db.models import Count
from django.db.models.signals import m2m_changed, post_save, pre_save
from django.dispatch import receiver

from base.models import Endorsement, PointsEntry, Profile, VenueNote
from education.models import Quiz, TrainingModule, TrainingModuleCompletion, UserCertification
from events.models import Event, EventFeedback, EventRoleSlot, EventSlotInvite
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


def _before(instance, fields, update_fields=None):
    """The saved values of `fields` for a row about to be saved again: None for a new row, or
    when the save doesn't touch any of them."""
    if not instance.pk:
        return None
    if update_fields is not None:  # a foreign key is saved as 'lead' or 'lead_id'
        touched = {name.removesuffix('_id') for name in update_fields}
        if not touched & {name.removesuffix('_id') for name in fields}:
            return None
    return type(instance).objects.filter(pk=instance.pk).values(*fields).first()


def _sides(instance, reverse, pk_set):
    """(ids on the model that owns the M2M field, ids on the other side), whichever side changed it."""
    return (list(pk_set), [instance.pk]) if reverse else ([instance.pk], list(pk_set))


def _about(user, **data):
    """(subject, data) for an event about a person."""
    return events.person_subject(user), {'person': events.person_data(user), **data}


# -- volunteer.registered / deactivated / reactivated / email_changed ----------

@receiver(pre_save, sender=User)
@hook
def remember_account(sender, instance, update_fields=None, **kwargs):
    instance._switchboard_before = _before(instance, ('is_active', 'email'), update_fields)


@receiver(post_save, sender=User)
@hook
def user_saved(sender, instance, created, **kwargs):
    before = getattr(instance, '_switchboard_before', None)
    instance._switchboard_before = None
    if created:
        events.emit('volunteer.registered', events.person_subject(instance), {
            'person': events.person_data(instance),
            'registered_at': events.iso(instance.date_joined),
        })
        return
    if before is None:
        return
    if before['is_active'] != instance.is_active:
        events.emit('volunteer.deactivated' if before['is_active'] else 'volunteer.reactivated', *_about(instance))
    if (before['email'] or '').lower() != (instance.email or '').lower():
        events.emit('volunteer.email_changed', *_about(instance, previous_email=before['email'] or ''))


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


# -- volunteer.role_assigned / role_unassigned ---------------------------------

@receiver(m2m_changed, sender=Profile.permanent_roles.through)
@hook
def permanent_roles_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ('post_add', 'post_remove') or not pk_set:
        return
    from jobs.models import Role
    profile_ids, role_ids = _sides(instance, reverse, pk_set)
    event_type = 'volunteer.role_assigned' if action == 'post_add' else 'volunteer.role_unassigned'
    roles = list(Role.objects.filter(pk__in=role_ids))
    for profile in Profile.objects.filter(pk__in=profile_ids).select_related('user'):
        for role in roles:
            events.emit(event_type, *_about(profile.user, role={'id': str(role.pk), 'name': role.name}))


# -- points.awarded / volunteer.profile_completed / shift.completed ------------
# A shift is completed when it gets its end time. Its points only exist once Shift.save() has
# written the ledger entry (base.points.award_shift, with this same Shift object), so the event
# goes out from there; recalculating old shifts later never sends it again.

@receiver(post_save, sender=PointsEntry)
@hook
def points_recorded(sender, instance, created, **kwargs):
    if not created:
        return  # a finished shift's entry being worked out again is not new points
    from django.conf import settings
    from django.utils import translation
    user = instance.user
    total = Profile.objects.filter(user_id=user.pk).values_list('impact_points', flat=True).first() or 0
    with translation.override(settings.LANGUAGE_CODE):  # "Greeter at Christmas Bazaar", not in whoever's language
        reason = str(instance.label)[:200]
    events.emit('points.awarded', *_about(
        user, points=instance.amount, source=instance.source, reason=reason, impact_points=total,
    ), occurred_at=instance.created_at if instance.source == PointsEntry.SHIFT else None)
    if instance.source == PointsEntry.PROFILE:
        events.emit('volunteer.profile_completed', *_about(user, points=instance.amount))


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


# -- shift.started -------------------------------------------------------------

@receiver(post_save, sender=Shift)
@hook
def shift_started(sender, instance, created, **kwargs):
    if not created or instance.end_time:
        return  # a finished shift entered afterwards was never a check-in
    slot = instance.event_role_slot if instance.event_role_slot_id else None
    role = instance.role or (slot.role if slot else None)
    events.emit('shift.started', *_about(
        instance.user,
        shift={'id': str(slot.pk) if slot else '', 'role': role.name if role else '',
               'started_at': events.iso(instance.start_time)},
        walk_in=bool(instance.clutched),
        event={'id': str(slot.event_id) if slot else '', 'title': slot.event.title if slot else '',
               'place': (slot.event.place_name or '') if slot else ''},
    ), occurred_at=instance.start_time)


# -- endorsement.given ---------------------------------------------------------
# An endorsement gets its skills right after it is created, and a teammate's one-click chips
# add more to it later: each time skills are added is one endorsement for those skills.

@receiver(m2m_changed, sender=Endorsement.skills.through)
@hook
def endorsement_given(sender, instance, action, reverse, pk_set, **kwargs):
    if action != 'post_add' or reverse or not pk_set:
        return
    from education.models import Skill
    endorser, endorsed, event = instance.endorser, instance.endorsed, instance.event
    events.emit('endorsement.given', *_about(
        endorsed,
        endorser=events.person_data(endorser),
        skills=', '.join(Skill.objects.filter(pk__in=pk_set).order_by('name').values_list('name', flat=True)),
        message=instance.text or '',
        event={'id': str(event.pk) if event else '', 'title': event.title if event else ''},
    ))


# -- shift.signed_up / shift.cancelled / shift.filled / area.lead_assigned ---------

@receiver(m2m_changed, sender=EventRoleSlot.signups.through)
@hook
def signups_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ('post_add', 'post_remove') or not pk_set:
        return
    from events.reliability import is_late
    # slot.signups.add(user) or user.commitments.add(slot): either side can be the instance.
    slot_ids, user_ids = _sides(instance, reverse, pk_set)
    added = action == 'post_add'
    users = list(User.objects.filter(pk__in=user_ids))
    slots = (EventRoleSlot.objects.filter(pk__in=slot_ids).select_related('event__venue', 'role', 'area')
             .annotate(taken=Count('signups')))
    for slot in slots:
        data = events.slot_data(slot, slot.taken)
        for user in users:
            if added:
                events.emit('shift.signed_up', *_about(user, **data))
                if slot.is_area_lead and slot.area_id:
                    events.emit('area.lead_assigned', *_about(
                        user, **data, area={'id': str(slot.area_id), 'name': slot.area.name}))
            else:
                events.emit('shift.cancelled', *_about(user, **data, late=is_late(slot)))
        # The sign-up that brought it to the number it needs (not every one after that).
        if added and not slot.is_area_lead and slot.taken - len(users) < slot.required_qty <= slot.taken:
            events.emit('shift.filled', events.slot_subject(slot), {**data, 'volunteers': events.slot_volunteer_items(slot)[0]})


# -- shift.lead_assigned -------------------------------------------------------

@receiver(pre_save, sender=EventRoleSlot)
@hook
def remember_lead(sender, instance, update_fields=None, **kwargs):
    instance._switchboard_before = _before(instance, ('lead_id',), update_fields)


@receiver(post_save, sender=EventRoleSlot)
@hook
def lead_assigned(sender, instance, created, **kwargs):
    before = getattr(instance, '_switchboard_before', None)
    instance._switchboard_before = None
    if not instance.lead_id or (not created and (before is None or before['lead_id'] == instance.lead_id)):
        return
    events.emit('shift.lead_assigned', *_about(
        instance.lead, **events.slot_data(instance), automatic=bool(instance.lead_auto_assigned)))


# -- invite.sent / invite.accepted (invite.declined: events.invite_declined) ----

@receiver(pre_save, sender=EventSlotInvite)
@hook
def remember_invite(sender, instance, update_fields=None, **kwargs):
    instance._switchboard_before = _before(instance, ('accepted',), update_fields)


@receiver(post_save, sender=EventSlotInvite)
@hook
def invite_saved(sender, instance, created, **kwargs):
    before = getattr(instance, '_switchboard_before', None)
    instance._switchboard_before = None
    if not instance.user_id:
        return
    slot = instance.event_role_slot
    if created and not instance.accepted:
        events.emit('invite.sent', *_about(instance.user, **events.slot_data(slot)))
    elif before and not before['accepted'] and instance.accepted and slot.user_signed_up(instance.user):
        # An invitation answered too late (the shift filled up or ended) is closed without a sign-up.
        events.emit('invite.accepted', *_about(instance.user, **events.slot_data(slot)))


# -- event.created / published / unpublished / rescheduled -------------------------

@receiver(pre_save, sender=Event)
@hook
def remember_event(sender, instance, **kwargs):
    instance._switchboard_before = _before(instance, ('published', 'start_date', 'end_date')) or {}


@receiver(post_save, sender=Event)
@hook
def event_saved(sender, instance, created, **kwargs):
    before = getattr(instance, '_switchboard_before', None)
    instance._switchboard_before = None
    if before is None:
        return
    subject, links = events.event_subject(instance), events.event_links(instance)
    data = events.event_data(instance)
    if created:
        events.emit('event.created', subject, {'event': {**data, 'published': bool(instance.published)}}, links=links)
    elif before and (before['start_date'] != instance.start_date or before['end_date'] != instance.end_date):
        events.emit('event.rescheduled', subject, {
            'event': {**data, 'published': bool(instance.published)},
            'previous_starts_at': events.iso(before['start_date']), 'previous_ends_at': events.iso(before['end_date']),
            **events.event_lists(instance, shifts=False),
        }, links=links)
    was_published = bool(before.get('published'))
    if instance.published and not was_published:
        events.emit('event.published', subject, {'event': data, **events.event_lists(instance, volunteers=False)}, links=links)
    elif was_published and not instance.published:
        events.emit('event.unpublished', subject, {'event': data, **events.event_lists(instance, shifts=False)}, links=links)


# -- event.coordinator_added / event.feedback_submitted ----------------------------

@receiver(m2m_changed, sender=Event.coordinators.through)
@hook
def coordinators_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action != 'post_add' or not pk_set:
        return
    event_ids, user_ids = _sides(instance, reverse, pk_set)
    users = list(User.objects.filter(pk__in=user_ids))
    for event in Event.objects.filter(pk__in=event_ids).select_related('venue'):
        for user in users:
            events.emit('event.coordinator_added', *_about(user, event=events.event_data(event)),
                        links=events.event_links(event))


@receiver(post_save, sender=EventFeedback)
@hook
def feedback_submitted(sender, instance, created, **kwargs):
    if not created:
        return
    event = instance.event
    events.emit('event.feedback_submitted', *_about(
        instance.user, event={'id': str(event.pk), 'title': event.title}, rating=instance.rating,
        enjoyed=instance.enjoyed or '', suggestions=instance.suggestions or ''))


# -- training.published / started / quiz_passed / completed ------------------------
# Staff-only modules (the admin guide) are not volunteer training: nothing is sent for them.

@receiver(pre_save, sender=TrainingModule)
@hook
def remember_module(sender, instance, update_fields=None, **kwargs):
    instance._switchboard_before = _before(instance, ('published',), update_fields) or {}


@receiver(post_save, sender=TrainingModule)
@hook
def module_published(sender, instance, created, **kwargs):
    before = getattr(instance, '_switchboard_before', None)
    instance._switchboard_before = None
    if before is None or instance.staff_only or not instance.published:
        return
    if created or before.get('published') is False:
        events.emit('training.published', {'type': 'training_module', 'id': str(instance.pk), 'name': instance.title},
                    {'module': events.module_data(instance)},
                    links={'module.url': events.site_path('module_overview', instance.pk)})


@receiver(m2m_changed, sender=TrainingModule.started_by.through)
@hook
def training_started(sender, instance, action, reverse, pk_set, **kwargs):
    if action != 'post_add' or not pk_set:
        return
    module_ids, user_ids = _sides(instance, reverse, pk_set)
    users = list(User.objects.filter(pk__in=user_ids))
    for module in TrainingModule.objects.filter(pk__in=module_ids, staff_only=False):
        for user in users:
            events.emit('training.started', *_about(user, module=events.module_data(module)))


@receiver(m2m_changed, sender=Quiz.completed_by.through)
@hook
def quiz_passed(sender, instance, action, reverse, pk_set, **kwargs):
    if action != 'post_add' or not pk_set:
        return
    quiz_ids, user_ids = _sides(instance, reverse, pk_set)
    users = list(User.objects.filter(pk__in=user_ids))
    for quiz in Quiz.objects.filter(pk__in=quiz_ids, training_module__staff_only=False).select_related('training_module'):
        for user in users:
            events.emit('training.quiz_passed', *_about(
                user, module=events.module_data(quiz.training_module), quiz={'id': str(quiz.pk), 'title': quiz.title}))


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


# -- certificate.submitted / approved / rejected -------------------------------------

@receiver(pre_save, sender=UserCertification)
@hook
def remember_certification(sender, instance, update_fields=None, **kwargs):
    instance._switchboard_before = _before(instance, ('verified', 'rejected'), update_fields) or {}


@receiver(post_save, sender=UserCertification)
@hook
def certification_saved(sender, instance, created, **kwargs):
    before = getattr(instance, '_switchboard_before', None)
    instance._switchboard_before = None
    if before is None or (not created and not before):
        return
    certificate = events.certificate_data(instance.certificate)
    was_verified, was_rejected = bool(before.get('verified')), bool(before.get('rejected'))
    if instance.verified and not was_verified:
        events.emit('certificate.approved', *_about(
            instance.user, certificate=certificate, expires_at=events.when(instance.expiration_date)))
    elif instance.rejected and not was_rejected:
        events.emit('certificate.rejected', *_about(instance.user, certificate=certificate, notes=instance.admin_notes or ''))
    elif not instance.verified and not instance.rejected and (created or was_rejected):
        # Sent in for the first time, or again after a rejection (which puts it back in the queue).
        events.emit('certificate.submitted', *_about(instance.user, certificate=certificate, resubmitted=not created))


# -- venue.note_posted ---------------------------------------------------------

@receiver(post_save, sender=VenueNote)
@hook
def venue_note_posted(sender, instance, created, **kwargs):
    if not created:
        return
    venue = instance.venue
    events.emit('venue.note_posted', {'type': 'venue', 'id': str(venue.pk), 'name': venue.name}, {
        'venue': {'id': str(venue.pk), 'name': venue.name},
        'note': instance.text,
        'expires_at': events.when(instance.expires_at),
    })
