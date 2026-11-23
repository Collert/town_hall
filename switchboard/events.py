"""Emitting events to Switchboard.

`emit()` writes one `Delivery` per connected Switchboard (the envelope, ready to sign) and
queues the sending for after the transaction commits, off the request thread
(switchboard/delivery.py). Nothing here may break or slow down what caused the event: with no
connection it costs one cheap query, and every failure is logged, never raised.

The `*_data` helpers build the `data` shapes the manifest's dotted field keys describe. A
field with nothing to say is '' for text and None (left out by Switchboard) for a time.
"""
import json
import logging
import secrets

from django.db import transaction
from django.utils import timezone

from . import manifest

logger = logging.getLogger(__name__)


def iso(value):
    """ISO 8601 with the organization's UTC offset, or '' for no time."""
    return timezone.localtime(value).isoformat(timespec='seconds') if value else ''


def when(value):
    """Like `iso`, but None for no time: an empty text is not a time the manifest allows."""
    return iso(value) or None


def is_connected():
    from .models import SwitchboardConnection
    return SwitchboardConnection.objects.active().exists()


def person_name(user):
    return user.get_full_name() or user.username


def person_data(user):
    return {'id': str(user.pk), 'name': person_name(user), 'email': user.email}


def person_subject(user):
    return {'type': 'person', **person_data(user)}


def levels():
    """Every level, lowest first: a level's number is its place in this list, from 1."""
    from base.models import Level
    return list(Level.objects.order_by('min_points', 'pk'))


def level_of(points, ordered=None):
    """(number, Level) for a points total; (0, None) below the lowest level."""
    number, current = 0, None
    for index, level in enumerate(levels() if ordered is None else ordered, start=1):
        if level.min_points <= points:
            number, current = index, level
    return number, current


def slot_data(slot, taken=None):
    """A role slot of an event and how its staffing stands (`taken`: its sign-ups, if already counted)."""
    event = slot.event
    taken = slot.signups.count() if taken is None else taken
    return {
        'shift': {'id': str(slot.pk), 'role': slot.role.name, 'role_id': slot.role.pk, 'starts_at': iso(slot.start_time), 'ends_at': iso(slot.end_time),
                  'area': slot.area.name if slot.area_id else '', 'needed': slot.required_qty, 'taken': taken,
                  'open_spots': max(0, slot.required_qty - taken)},
        'event': {'id': str(event.pk), 'title': event.title, 'place': event.place_name or ''},
    }


def slot_subject(slot):
    return {'type': 'shift', 'id': str(slot.pk), 'name': f'{slot.role.name} at {slot.event.title}'}


def event_data(event):
    """An event, without its link (see `event_links`)."""
    return {'id': str(event.pk), 'title': event.title, 'place': event.place_name or '',
            'starts_at': iso(event.start_date), 'ends_at': iso(event.end_date)}


def event_subject(event):
    return {'type': 'event', 'id': str(event.pk), 'name': event.title}


def site_path(name, *args):
    """A page's path in the organization's default language (events aren't sent from a request)."""
    from django.conf import settings
    from django.urls import reverse
    from django.utils import translation
    with translation.override(settings.LANGUAGE_CODE):
        return reverse(name, args=args)


def event_links(event):
    return {'event.url': site_path('opportunity_detail', event.pk)}


def event_staffing(event):
    needed, taken, open_spots = 0, 0, 0
    from django.db.models import Count
    for required, count in (event.role_slots.exclude(role__system_key__isnull=False)
                            .annotate(n=Count('signups')).values_list('required_qty', 'n')):
        needed, taken, open_spots = needed + required, taken + count, open_spots + max(0, required - count)
    return {'volunteers_needed': needed, 'volunteers_signed_up': taken, 'open_spots': open_spots}


def module_data(module):
    return {'id': str(module.pk), 'title': module.title}


def certificate_data(certificate):
    return {'id': str(certificate.pk), 'name': certificate.name}


def invite_declined(invite):
    """A volunteer said no to an invitation. Called by the one view where that happens
    (events.views.respond_to_invite) just before the invitation is deleted: a deleted row alone
    can't tell a decline from an event's clean-up. Never raises."""
    try:
        if invite.user_id and is_connected():
            emit('invite.declined', person_subject(invite.user),
                 {'person': person_data(invite.user), **slot_data(invite.event_role_slot)})
    except Exception:
        logger.exception('Switchboard: invite.declined failed')


def _summary(event_type, data):
    """The manifest's one-line summary, filled in, for the delivery log."""
    text = manifest.TRIGGERS_BY_TYPE[event_type]['summary']
    for spec in manifest.TRIGGERS_BY_TYPE[event_type]['fields']:
        value = data
        for part in spec['key'].split('.'):
            value = value.get(part, '') if isinstance(value, dict) else ''
        text = text.replace('{%s}' % spec['key'], str(value))
    return text[:200]


def _with_links(data, links, base_url):
    """`data` with each dotted key of `links` set to the full address of its path."""
    data = json.loads(json.dumps(data))
    for key, path in links.items():
        target = data
        *parents, last = key.split('.')
        for part in parents:
            target = target.setdefault(part, {})
        target[last] = base_url + path
    return data


def emit(event_type, subject, data, links=None, occurred_at=None):
    """Queue an event for every connected Switchboard. `links` maps dotted data keys to site
    paths; each connection gets them as full addresses of the Town Hall it connected to."""
    try:
        from .models import Delivery, SwitchboardConnection
        connections = list(SwitchboardConnection.objects.active())
        if not connections:
            return None
        from . import delivery
        event_id = 'evt_' + secrets.token_hex(10)
        now = timezone.now()
        ids = []
        with transaction.atomic():  # a savepoint, so a failure here can't spoil the caller's transaction
            for connection in connections:
                event_data = _with_links(data, links, connection.base_url) if links else data
                envelope = {
                    'id': event_id,
                    'type': event_type,
                    'source': {'building': manifest.BUILDING, 'instance': connection.base_url},
                    'occurred_at': iso(occurred_at or now),
                    'subject': subject,
                    'data': event_data,
                }
                ids.append(Delivery.objects.create(
                    connection=connection, event_id=event_id, event_type=event_type,
                    summary=_summary(event_type, event_data), body=json.dumps(envelope, ensure_ascii=False),
                    created_at=now, next_attempt_at=now,
                ).pk)
        delivery.run_after_commit(delivery.send, ids)
        return event_id
    except Exception:
        logger.exception('Switchboard: could not queue %s', event_type)
        return None
