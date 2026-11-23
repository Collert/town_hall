"""Events that happen because time passes, not because somebody saved something: an event
starting, a shift still short of volunteers, a volunteer who never checked in, a certificate
about to expire.

`run_due()` looks for what has become true and emits each once. It runs like Town Hall's
other time-based jobs (shift reminders, lead deadlines): from throttled middleware as requests
come in (`run_due_if_idle`, at most every CHECK_EVERY seconds, in a thread so no page waits for
it) and from `manage.py send_switchboard_deliveries`, so it works without a scheduler.

Exactly once: an `EmittedOnce` row (the trigger type and what it is about) is created in the
same transaction as the event's deliveries, and its unique pair turns a second worker away. Things that became true
before a Switchboard was connected, or more than LOOK_BACK ago, are left alone, so connecting
never sets off a flood about the past.
"""
import logging
import threading
from datetime import timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Min
from django.utils import timezone

from . import events

logger = logging.getLogger(__name__)

CHECK_EVERY = 300                       # seconds between passes from middleware
LOOK_BACK = timedelta(days=7)           # how late a pass may still report something
SOON = timedelta(hours=24)              # "starts within 24 hours"
UNDERSTAFFED_BEFORE = timedelta(hours=48)
CERTIFICATE_WARNING = timedelta(days=30)
KEEP_MARKS = timedelta(days=60)


class _NotQueued(Exception):
    pass


def once(event_type, key, subject, data, **emit_kwargs):
    """Emit unless this event already went out for `key`. True when this call sent it."""
    from .models import EmittedOnce
    try:
        with transaction.atomic():
            EmittedOnce.objects.create(kind=event_type, key=key)
            if events.emit(event_type, subject, data, **emit_kwargs) is None:
                raise _NotQueued  # nothing was queued: take the mark back so the next pass tries again
    except IntegrityError:
        return False  # already sent: another worker got there first
    except _NotQueued:
        return False
    return True


class Sender:
    """Sends one kind of time-based event during a pass. What already went out is read once,
    so something that stays true for a day (a shift that starts tomorrow, for each of its
    volunteers) costs one query per pass rather than a refused insert each."""

    def __init__(self, event_type):
        from .models import EmittedOnce
        self.event_type, self.sent = event_type, 0
        self.done = set(EmittedOnce.objects.filter(kind=event_type).values_list('key', flat=True))

    def wants(self, key):
        """Whether this is still to send: ask before building data that costs queries (lists)."""
        return str(key)[:100] not in self.done

    def __call__(self, key, subject, data, **emit_kwargs):
        key = str(key)[:100]
        if key not in self.done:
            self.sent += once(self.event_type, key, subject, data, **emit_kwargs)


def _hours_until(start, now):
    return round(max(0.0, (start - now).total_seconds() / 3600), 1)


def _about(user, **data):
    return events.person_subject(user), {'person': events.person_data(user), **data}


def _slots(**filters):
    from events.models import EventRoleSlot
    return (EventRoleSlot.objects.filter(event__published=True, **filters)
            .select_related('event__venue', 'role', 'area').annotate(taken=Count('signups')))


# -- events ------------------------------------------------------------------

def events_starting_soon(now, since):
    from events.models import Event
    send = Sender('event.starting_soon')
    for event in Event.objects.filter(published=True, start_date__gt=now, start_date__lte=now + SOON).select_related('venue'):
        # Keyed by its start as well: an event moved to another day is announced again.
        key = f'{event.pk}:{events.iso(event.start_date)}'
        if send.wants(key):
            send(key, events.event_subject(event),
                 {'event': events.event_data(event), **events.event_staffing(event), **events.event_lists(event),
                  'hours_until': _hours_until(event.start_date, now)},
                 links=events.event_links(event))
    return send.sent


def events_started(now, since):
    from events.models import Event
    send = Sender('event.started')
    for event in Event.objects.filter(published=True, start_date__gte=since, start_date__lte=now, end_date__gt=now).select_related('venue'):
        if send.wants(event.pk):
            send(event.pk, events.event_subject(event),
                 {'event': events.event_data(event), **events.event_staffing(event), **events.event_lists(event)},
                 links=events.event_links(event), occurred_at=event.start_date)
    return send.sent


def events_ended(now, since):
    from events.models import Event
    send = Sender('event.ended')
    for event in Event.objects.filter(published=True, end_date__gte=since, end_date__lte=now).select_related('venue'):
        if not send.wants(event.pk):
            continue
        shifts = list(event.shifts().select_related('user', 'role', 'event_role_slot__role'))
        hours = sum(((shift.end_time or now) - shift.start_time).total_seconds() for shift in shifts) / 3600
        send(event.pk, events.event_subject(event), {
            'event': events.event_data(event),
            'volunteers': len({shift.user_id for shift in shifts}),
            'hours': round(hours, 2),
            'volunteers_signed_up': events.event_staffing(event)['volunteers_signed_up'],
            'people': _worked(shifts, now),
        }, links=events.event_links(event), occurred_at=event.end_date)
    return send.sent


def _worked(shifts, now):
    """Who checked in at an event, by name, each with the hours logged so far and their roles."""
    people = {}
    for shift in shifts:
        person = people.setdefault(shift.user_id, {'user': shift.user, 'seconds': 0, 'roles': {}})
        person['seconds'] += ((shift.end_time or now) - shift.start_time).total_seconds()
        role = shift.role or (shift.event_role_slot.role if shift.event_role_slot_id else None)
        if role:
            person['roles'][role.name] = True
    ordered = sorted(people.values(), key=lambda p: (p['user'].first_name, p['user'].last_name, p['user'].pk))
    return [{**events.person_item(p['user']), 'role': ', '.join(p['roles']), 'hours': round(p['seconds'] / 3600, 2)}
            for p in ordered[:events.LIST_LIMIT]]


# -- shifts ------------------------------------------------------------------

def shifts_understaffed(now, since):
    send = Sender('shift.understaffed')
    slots = (_slots(start_time__gt=now, start_time__lte=now + UNDERSTAFFED_BEFORE)
             .exclude(role__system_key__isnull=False).filter(taken__lt=F('required_qty')))
    for slot in slots:
        if send.wants(slot.pk):
            send(slot.pk, events.slot_subject(slot),
                 {**events.slot_data(slot, slot.taken), 'hours_until': _hours_until(slot.start_time, now),
                  'volunteers': events.slot_volunteer_items(slot)[0]})
    return send.sent


def shifts_starting_soon(now, since):
    send = Sender('shift.starting_soon')
    for slot in _slots(start_time__gt=now, start_time__lte=now + SOON, signups__isnull=False).prefetch_related('signups'):
        data = {**events.slot_data(slot, slot.taken), 'hours_until': _hours_until(slot.start_time, now)}
        for user in slot.signups.all():
            if user.is_active:
                send(f'{slot.pk}:{user.pk}', *_about(user, **data))
    return send.sent


def shift_no_shows(now, since):
    """The reliability score's rule (events/reliability.py): still signed up when the shift
    ended, and never checked in or only after half of it had passed."""
    from events.reliability import showed_up
    from jobs.models import Shift
    send = Sender('shift.no_show')
    slots = list(_slots(end_time__gte=since, end_time__lte=now, signups__isnull=False).prefetch_related('signups'))
    arrived = {(slot_id, user_id): first for slot_id, user_id, first in (
        Shift.objects.filter(event_role_slot__in=[slot.pk for slot in slots])
        .values('event_role_slot_id', 'user_id').annotate(first=Min('start_time'))
        .values_list('event_role_slot_id', 'user_id', 'first'))}
    for slot in slots:
        data = events.slot_data(slot, slot.taken)
        for user in slot.signups.all():
            checked_in_at = arrived.get((slot.pk, user.pk))
            if not showed_up(slot, checked_in_at):
                send(f'{slot.pk}:{user.pk}', *_about(user, **data, checked_in_late=checked_in_at is not None),
                     occurred_at=slot.end_time)
    return send.sent


# -- training and certificates -------------------------------------------------

def training_expired(now, since):
    from education.models import TrainingModule, TrainingModuleCompletion
    send = Sender('training.expired')
    for module in TrainingModule.objects.filter(expires_after_days__gt=0, staff_only=False):
        lasts = timedelta(days=module.expires_after_days)
        completions = (TrainingModuleCompletion.objects.filter(
            training_module=module, completed_at__gte=since - lasts, completed_at__lte=now - lasts).select_related('user'))
        for completion in completions:
            expired_at = completion.completed_at + lasts
            send(f'{completion.pk}:{events.iso(completion.completed_at)}', *_about(
                completion.user, module=events.module_data(module),
                completed_at=events.iso(completion.completed_at), expired_at=events.iso(expired_at),
            ), occurred_at=expired_at)
    return send.sent


def _certifications(**filters):
    from education.models import UserCertification
    return UserCertification.objects.filter(verified=True, user__is_active=True, **filters).select_related('user', 'certificate')


def certificates_expiring(now, since):
    send = Sender('certificate.expiring')
    for held in _certifications(expiration_date__gt=now, expiration_date__lte=now + CERTIFICATE_WARNING):
        send(f'{held.pk}:{events.iso(held.expiration_date)}', *_about(
            held.user, certificate=events.certificate_data(held.certificate), expires_at=events.iso(held.expiration_date),
            days_left=max(0, (held.expiration_date - now).days)))
    return send.sent


def certificates_expired(now, since):
    send = Sender('certificate.expired')
    for held in _certifications(expiration_date__gte=since, expiration_date__lte=now):
        send(f'{held.pk}:{events.iso(held.expiration_date)}', *_about(
            held.user, certificate=events.certificate_data(held.certificate), expires_at=events.iso(held.expiration_date),
        ), occurred_at=held.expiration_date)
    return send.sent


CHECKS = (events_starting_soon, events_started, events_ended, shifts_understaffed, shifts_starting_soon,
          shift_no_shows, training_expired, certificates_expiring, certificates_expired)


def run_due(now=None):
    """Emit every time-based event that has come due. Returns how many went out."""
    from .models import SwitchboardConnection
    now = now or timezone.now()
    connected_since = SwitchboardConnection.objects.active().aggregate(first=Min('created_at'))['first']
    if connected_since is None:
        return 0
    since = max(connected_since, now - LOOK_BACK)
    sent = 0
    for check in CHECKS:
        try:
            sent += check(now, since)
        except Exception:  # one kind failing must not hold up the others
            logger.exception('Switchboard: %s failed', check.__name__)
    return sent


def prune(now=None):
    from .models import EmittedOnce
    EmittedOnce.objects.filter(created_at__lt=(now or timezone.now()) - KEEP_MARKS).delete()


def run_due_if_idle():
    """From middleware: at most every CHECK_EVERY seconds, look for what has come due, in a thread."""
    if not cache.add('switchboard_timed_check', True, CHECK_EVERY):
        return
    try:
        if events.is_connected():
            from .delivery import _inline, _run
            if _inline():  # tests: on the calling thread, like deliveries
                _run(run_due)
            else:
                threading.Thread(target=_run, args=(run_due,), daemon=True).start()
    except Exception:  # never break a page over this
        logger.exception('Switchboard: checking for time-based events failed')
