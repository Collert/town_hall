"""The actions Switchboard can ask Town Hall to run (the manifest's `actions`).

Each takes the connection, the checked params (already of their manifest types and within
their limits, see views._check_params) and the request, and returns the JSON object the
manifest's `output` describes. `ActionError` becomes the protocol's error body; its message is
shown to a non-technical admin in Switchboard's run log.

What they hold to:

- People are matched by email, ignoring case. A lookup that doesn't find what it looks for
  answers `{"found": false}`; a role, module, venue or skill named in a setting that doesn't
  exist is a 422, because that is a typing mistake in the workflow, not a branch.
- Nothing is deleted, and nothing gets past a rule the app has for people: signing up goes
  through the training, certificate, capacity and clash checks of the volunteer's own sign-up,
  cancelling through events/reliability.py, points through the ledger (base/points.py), and
  everything else calls what the console or the AI connector calls.
- A lookup that can match several answers how many matched (`count`), the first match in
  full, names joined into text, and the matches as a list a workflow can loop over: at most
  `events.LIST_LIMIT` items in the order the manifest gives, `[]` when nothing matched. An
  item holds what a later step needs to act on it (IDs, a name, an email for a person), and
  only what the same lookups already tell about one of them. Lists are built with a fixed
  number of queries, never one per item.
"""
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Count, F, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from . import events

LIST_MAX = 50  # names or emails joined into one text field
LIST_LIMIT = events.LIST_LIMIT  # items in a list


class ActionError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def invalid(message):
    return ActionError(422, 'invalid_params', message)


def refused(message):
    return ActionError(409, 'conflict', message)


def _clean(output):
    """Without the fields that have nothing to say (a time that isn't set)."""
    return {key: value for key, value in output.items() if value is not None}


def _joined(values):
    return ', '.join(str(value) for value in list(values)[:LIST_MAX] if value)


# ---------------------------------------------------------------------------
# Finding what a call is about
# ---------------------------------------------------------------------------

def find_by_email(email):
    """The account with this email (the active one first, then the oldest), or None."""
    return User.objects.filter(email__iexact=email).order_by('-is_active', 'pk').first()


def _email(params):
    email = params['email'].strip()
    try:
        validate_email(email)
    except ValidationError:
        raise invalid(f'"{email}" is not an email address.')
    return email


def _by_id(queryset, value):
    value = str(value).strip()
    return queryset.filter(pk=int(value)).first() if value.isdigit() else None


def _person(params, key='person_id'):
    return _by_id(User.objects.select_related('profile'), params[key])


def _need_person(params, key='person_id'):
    user = _person(params, key)
    if not user:
        raise invalid(f'There is no volunteer with person ID "{params[key]}".')
    return user


def _named(queryset, value, what, field='name'):
    """What a setting names: by ID, or by its exact name (ignoring case) in the organization's
    default language. None when there is none; a 422 when the name fits several."""
    value = ' '.join(str(value).split())
    item = _by_id(queryset, value)
    if item:
        return item
    matches = list(queryset.filter(**{f'{field}__iexact': value})[:2])
    if len(matches) > 1:
        raise invalid(f'More than one {what} is called "{value}". Use its ID instead.')
    return matches[0] if matches else None


def _need_named(queryset, value, what, field='name'):
    item = _named(queryset, value, what, field)
    if not item:
        raise invalid(f'Town Hall has no {what} called "{value}" (or with that ID). Check the spelling in Town Hall.')
    return item


def _roles():
    """Roles people can be given or shifts can be added for: not the built-in ones (Area lead)."""
    from jobs.models import Role
    return Role.objects.filter(system_key__isnull=True)


def _permanent_role(value):
    role = _need_named(_roles(), value, 'role')
    if not role.permanent:
        raise invalid(f'"{role.name}" is an event role, not a permanent one. For an event, use a shift of that role.')
    return role


def _slots():
    from events.models import EventRoleSlot
    return EventRoleSlot.objects.select_related('event__venue', 'role', 'area', 'lead')


def _need_slot(params):
    slot = _by_id(_slots(), params['shift_id'])
    if not slot:
        raise invalid(f'There is no shift with ID "{params["shift_id"]}".')
    return slot


def _events():
    from events.models import Event
    return Event.objects.select_related('venue')


def _need_event(params, key='event_id'):
    event = _by_id(_events(), params[key])
    if not event:
        raise invalid(f'There is no event with ID "{params[key]}".')
    return event


def _open_shift(user):
    from jobs.models import Shift
    return (Shift.objects.filter(user=user, end_time__isnull=True)
            .select_related('role', 'event_role_slot__event', 'event_role_slot__role').first())


# ---------------------------------------------------------------------------
# What answers are made of
# ---------------------------------------------------------------------------

def person_brief(user):
    return {'person_id': str(user.pk), 'name': events.person_name(user), 'email': user.email}


def person_output(user):
    from events import reliability
    profile = user.profile
    number, level = events.level_of(profile.impact_points)
    return {
        'found': True,
        'person_id': str(user.pk),
        'name': events.person_name(user),
        'email': user.email,
        'level': number,
        'level_name': level.name if level else '',
        'impact_points': profile.impact_points,
        'total_hours': profile.hours_contributed(),
        'active': user.is_active,
        'staff': user.is_staff,
        'phone': profile.phone,
        'location': profile.location,
        'skills': _joined(profile.skills.order_by('name').values_list('name', flat=True)),
        'permanent_roles': _joined(profile.permanent_roles.order_by('name').values_list('name', flat=True)),
        'reliability': reliability.for_user(user).score,
        'joined_at': events.iso(user.date_joined),
    }


def slot_brief(slot):
    event = slot.event
    return {
        'shift_id': str(slot.pk), 'role': slot.role.name,
        'starts_at': events.iso(slot.start_time), 'ends_at': events.iso(slot.end_time),
        'event_id': str(event.pk), 'event_title': event.title, 'place': event.place_name or '',
        'open_spots': slot.available_slots(),
    }


def slot_output(slot, people=None):
    people = list(slot.signups.order_by(*events.BY_NAME)) if people is None else people
    return {
        **slot_brief(slot),
        'open_spots': max(0, slot.required_qty - len(people)),
        'role_id': str(slot.role_id),
        'area': slot.area.name if slot.area_id else '',
        'needed': slot.required_qty,
        'taken': len(people),
        'invite_only': not slot.is_public,
        'lead_name': events.person_name(slot.lead) if slot.lead_id else '',
        'lead_email': slot.lead.email if slot.lead_id else '',
        'volunteers': _joined(events.person_name(person) for person in people),
        'volunteer_emails': _joined(person.email for person in people),
    }


def _event_url(request, event):
    return request.build_absolute_uri(reverse('opportunity_detail', args=[event.pk]))


def event_output(event, request):
    coordinators = list(event.coordinators.order_by('first_name', 'last_name', 'pk'))
    return {
        'event_id': str(event.pk), 'title': event.title,
        'place': event.place_name or '', 'address': event.address or '',
        'starts_at': events.iso(event.start_date), 'ends_at': events.iso(event.end_date),
        'published': event.published,
        'status': 'past' if event.is_past else 'live' if event.is_live else 'upcoming',
        'url': _event_url(request, event),
        **events.event_staffing(event),
        'coordinators': _joined(events.person_name(person) for person in coordinators),
        'coordinator_emails': _joined(person.email for person in coordinators),
        'volunteer_emails': _joined(event.volunteers().order_by('pk').values_list('email', flat=True)[:LIST_MAX]),
    }


def _listed(queryset, output, names, key, items):
    """A list lookup's answer: how many matched, the first in full, all their names as text
    (`names`, of the first LIST_MAX) and the matches as the list `key` (`items` turns the
    first LIST_LIMIT into its items)."""
    some, count = events.capped(queryset)
    if not count:
        return {'found': False, 'count': 0, key: []}
    return {'found': True, 'count': count, **output(some[0]), **names(some[:LIST_MAX]), key: items(some)}


def _found(key, items, count=None, **more):
    """The answer of a lookup that is only a list: `found` says whether it holds anything."""
    count = len(items) if count is None else count
    return {'found': bool(count), 'count': count, **more, key: items}


def _event_lists(event):
    shifts, shift_count = events.event_shift_items(event)
    volunteers, volunteer_count = events.event_volunteer_items(event)
    return {'shifts': shifts, 'shift_count': shift_count, 'volunteers': volunteers, 'volunteer_count': volunteer_count}


def _event_items(found, request):
    """Events as list items, with each one's staffing from one query for all of them."""
    from events.models import EventRoleSlot
    staffing = {event.pk: [0, 0, 0] for event in found}
    for event_id, required, taken in (
            EventRoleSlot.objects.filter(event__in=list(staffing)).exclude(role__system_key__isnull=False)
            .annotate(n=Count('signups')).values_list('event_id', 'required_qty', 'n')):
        numbers = staffing[event_id]
        numbers[0], numbers[1], numbers[2] = numbers[0] + required, numbers[1] + taken, numbers[2] + max(0, required - taken)
    return [{
        'event_id': str(event.pk), 'title': event.title, 'place': event.place_name or '',
        'starts_at': events.iso(event.start_date), 'ends_at': events.iso(event.end_date),
        'published': event.published, 'url': _event_url(request, event),
        'volunteers_needed': staffing[event.pk][0], 'volunteers_signed_up': staffing[event.pk][1],
        'open_spots': staffing[event.pk][2],
    } for event in found]


def _days_left(moment, now):
    return max(0, (moment - now).days)


# ---------------------------------------------------------------------------
# People: looking up
# ---------------------------------------------------------------------------

def person_find(connection, params, request):
    user = find_by_email(_email(params))
    return person_output(user) if user else {'found': False}


def person_get(connection, params, request):
    user = _person(params)
    return person_output(user) if user else {'found': False}


def person_search(connection, params, request):
    people = User.objects.filter(is_active=True).select_related('profile')
    for word in params.get('query', '').split():
        people = people.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word)
                               | Q(email__icontains=word) | Q(username__icontains=word))
    ordered = events.levels()
    if 'min_level' in params:
        if params['min_level'] > len(ordered):
            return {'found': False, 'count': 0, 'people': []}
        people = people.filter(profile__impact_points__gte=ordered[params['min_level'] - 1].min_points)
    if 'role' in params:
        people = people.filter(profile__permanent_roles=_permanent_role(params['role']))
    if 'event_id' in params:
        people = people.filter(commitments__event=_need_event(params))
    people = people.distinct().order_by('-profile__impact_points', 'pk')

    def item(user):
        number, level = events.level_of(user.profile.impact_points, ordered)
        return {**events.person_item(user), 'level': number, 'level_name': level.name if level else '',
                'impact_points': user.profile.impact_points}

    return _listed(people, lambda user: {key: value for key, value in person_output(user).items() if key != 'found'},
                   lambda some: {'names': _joined(events.person_name(user) for user in some),
                                 'emails': _joined(user.email for user in some)},
                   'people', lambda some: [item(user) for user in some])


def person_next_shift(connection, params, request):
    user = _person(params)
    if not user:
        return {'found': False}
    upcoming = _slots().filter(signups=user, end_time__gt=timezone.now()).order_by('start_time', 'pk')
    return _listed(upcoming, slot_brief, lambda some: {}, 'shifts', events.shift_items)


def shift_current(connection, params, request):
    user = _person(params)
    shift = _open_shift(user) if user else None
    if not shift:
        return {'found': False}
    slot = shift.event_role_slot if shift.event_role_slot_id else None
    return {
        'found': True, 'role': shift.role.name if shift.role_id else '', 'started_at': events.iso(shift.start_time),
        'shift_id': str(slot.pk) if slot else '',
        'event_id': str(slot.event_id) if slot else '', 'event_title': slot.event.title if slot else '',
    }


def role_check(connection, params, request):
    from education.models import TrainingModule, TrainingModuleCompletion
    user = _person(params)
    if not user:
        return {'found': False}
    role = _need_named(_roles(), params['role'], 'role')
    done = set(TrainingModuleCompletion.objects.filter(user=user).values_list('training_module_id', flat=True))
    required = TrainingModule.objects.filter(
        roletrainingrequirement__role=role, roletrainingrequirement__mandatory=True).distinct().order_by('title')
    modules = [module for module in required if module.pk not in done]
    lacking = role.missing_certificates(user)
    certificates = sorted(lacking, key=lambda certificate: (certificate.name, certificate.pk))
    missing = ([{'kind': 'training', 'id': str(module.pk), 'name': module.title,
                 'url': request.build_absolute_uri(reverse('module_overview', args=[module.pk]))} for module in modules]
               + [{'kind': 'certificate', 'id': str(certificate.pk), 'name': certificate.name, 'url': ''}
                  for certificate in certificates])
    return {
        'found': True,
        'qualified': role.meets_requirements(user),
        'missing_training': _joined(module.title for module in modules),
        'missing_certificates': _joined(certificate.name for certificate in lacking),
        'assigned': bool(role.permanent and role.is_assigned_to(user)),
        'role_id': str(role.pk), 'role': role.name,
        'missing': missing[:LIST_LIMIT], 'missing_count': len(missing),
    }


def training_status(connection, params, request):
    from education.models import TrainingModule, TrainingModuleCompletion
    user = _person(params)
    if not user:
        return {'found': False}
    module = _need_named(TrainingModule.objects.filter(staff_only=False), params['module'], 'training module', 'title')
    completion = TrainingModuleCompletion.objects.filter(training_module=module, user=user).first()
    expires_at = (completion.completed_at + timedelta(days=module.expires_after_days)
                  if completion and module.expires_after_days else None)
    return _clean({
        'found': True,
        'completed': bool(completion),
        'progress': 100 if completion else module.completion_percentage_for_user(user),
        'completed_at': events.when(completion.completed_at) if completion else None,
        'expired': bool(expires_at and expires_at < timezone.now()),
        'expires_at': events.when(expires_at),
        'module_id': str(module.pk), 'module_title': module.title,
    })


def certificate_status(connection, params, request):
    from education.models import ExternalCertificate, UserCertification
    user = _person(params)
    if not user:
        return {'found': False}
    certificate = _need_named(ExternalCertificate.objects, params['certificate'], 'certificate')
    held = UserCertification.objects.filter(user=user, certificate=certificate).first()
    return _clean({
        'found': True,
        'status': held.status if held else 'none',
        'valid': certificate.pk in UserCertification.valid_certificate_ids(user),
        'expires_at': events.when(held.expiration_date) if held and held.verified else None,
        'certificate_id': str(certificate.pk), 'certificate': certificate.name,
    })


# ---------------------------------------------------------------------------
# People: changing
# ---------------------------------------------------------------------------

def person_invite(connection, params, request):
    """Add a volunteer the way Console > Volunteers > Add does, except that they choose their
    own password: they get the welcome email and a link to set one."""
    from django.contrib.auth.tokens import default_token_generator
    from django.urls import reverse
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    from base.email import notify, send_welcome_email

    email, name = _email(params), ' '.join(params['name'].split())
    if not name:
        raise invalid('name is required.')
    user = find_by_email(email)
    if user:
        return {'person_id': str(user.pk), 'created': False, 'name': events.person_name(user), 'email': user.email}

    first_name, _sep, last_name = name.partition(' ')
    username = email[:150]
    if User.objects.filter(username=username).exists():
        username = f"{email.split('@')[0][:140]}_{secrets.token_hex(3)}"
    user = User.objects.create_user(
        username=username, email=email, password=secrets.token_urlsafe(24),  # nobody knows it; they set their own
        first_name=first_name[:150], last_name=last_name[:150],
    )
    send_welcome_email(request, user)
    notify('password_reset', user, {'link': request.build_absolute_uri(reverse('password_reset_confirm', kwargs={
        'uidb64': urlsafe_base64_encode(force_bytes(user.pk)), 'token': default_token_generator.make_token(user)}))})
    return {'person_id': str(user.pk), 'created': True, 'name': events.person_name(user), 'email': user.email}


def person_update(connection, params, request):
    """A person's basics, as on Console > Volunteers > Edit. Not their email (it is how the
    organization's apps match people), password or access."""
    user = _need_person(params)
    profile = user.profile
    given = [key for key in ('first_name', 'last_name', 'phone', 'location') if key in params]
    if not given:
        raise invalid('Fill in at least one of first_name, last_name, phone or location.')
    changed = []
    for key in given:
        target = user if key in ('first_name', 'last_name') else profile
        value = ' '.join(params[key].split())
        if getattr(target, key) != value:
            setattr(target, key, value)
            changed.append(key)
    user_fields = [key for key in changed if key in ('first_name', 'last_name')]
    profile_fields = [key for key in changed if key in ('phone', 'location')]
    if user_fields:
        user.save(update_fields=user_fields)
    if profile_fields:
        profile.save(update_fields=profile_fields)
    return {**person_brief(user), 'updated': bool(changed), 'changed': ', '.join(changed),
            'phone': profile.phone, 'location': profile.location}


def _set_active(params, active):
    """Console > Volunteers > Deactivate / Reactivate. Staff are left to Town Hall, so a
    workflow can never lock the people who run it out."""
    user = _need_person(params)
    if not active and (user.is_staff or user.is_superuser):
        raise refused(f'{events.person_name(user)} is staff. Staff accounts can only be deactivated in Town Hall.')
    changed = user.is_active != active
    if changed:
        user.is_active = active
        user.save(update_fields=['is_active'])
    return {**person_brief(user), 'active': user.is_active, 'changed': changed}


def person_deactivate(connection, params, request):
    return _set_active(params, False)


def person_reactivate(connection, params, request):
    return _set_active(params, True)


def points_award(connection, params, request):
    """A line in the points ledger, like Console > Volunteers > Adjust points."""
    from base import points
    user = _need_person(params)
    amount, reason = params['points'], ' '.join(params['reason'].split())
    if not amount:
        raise invalid('points must not be 0.')
    if not reason:
        raise invalid('reason is required.')
    entry = points.adjust(user, amount, reason, None)
    user.profile.refresh_from_db(fields=['impact_points'])
    number, level = events.level_of(user.profile.impact_points)
    return {'entry_id': str(entry.pk), 'person_id': str(user.pk), 'points': amount,
            'impact_points': user.profile.impact_points, 'level': number, 'level_name': level.name if level else ''}


def endorsement_give(connection, params, request):
    """One person endorsing another for a skill, as on the Endorse page: `Endorsement.give`
    (whose rules decide the points), the notification and the email."""
    from base.email import notify
    from base.models import Endorsement, Notification, PointsEntry
    from education.models import Skill

    endorsed, endorser = _need_person(params), _need_person(params, 'from_person_id')
    if endorsed.pk == endorser.pk:
        raise invalid("People can't endorse themselves: person_id and from_person_id are the same person.")
    for person in (endorsed, endorser):
        if not person.is_active:
            raise refused(f"{events.person_name(person)}'s account is deactivated.")
    skill = _need_named(Skill.objects, params['skill'], 'skill')
    event = _need_event(params) if 'event_id' in params else None
    earlier = Endorsement.objects.filter(endorser=endorser, endorsed=endorsed, skills=skill).first()
    if earlier:  # a person endorses each skill once
        return {'endorsement_id': str(earlier.pk), 'created': False, 'points': 0, 'skill': skill.name}

    message = params.get('message', '').strip()
    endorsement = Endorsement.give(endorser, endorsed, [skill], text=message, event=event)
    earned = PointsEntry.objects.filter(endorsement=endorsement).values_list('amount', flat=True).first() or 0
    link = request.build_absolute_uri(reverse('profile_view', args=[endorsed.username]))
    name = events.person_name(endorser)
    Notification.objects.create(
        user=endorsed, link=link,
        message=(_('%(name)s endorsed you for %(skill)s!') % {'name': name, 'skill': skill.name})[:255])
    notify('endorsement_received', endorsed, {'from_name': name, 'skills': skill.name, 'message': message, 'link': link})
    return {'endorsement_id': str(endorsement.pk), 'created': True, 'points': earned, 'skill': skill.name}


def notification_send(connection, params, request):
    """A notification like the one staff send from a volunteer's page in the console: in the
    site's bell, and by email and push where those are switched on."""
    from base.email import notify, text_to_html
    from base.models import Notification

    user = _need_person(params)
    if not user.is_active:
        raise refused(f"{events.person_name(user)}'s account is deactivated, so they can't be notified.")
    title, message = params['title'].strip(), params['message'].strip()
    if not title or not message:
        raise invalid('title and message are both required.')
    link = (params.get('link') or '').strip()
    if link.startswith('/') and not link.startswith('//'):
        link = request.build_absolute_uri(link)
    elif link and not link.startswith(('http://', 'https://')):
        raise invalid('link must be a full web address or start with "/".')
    if len(link) > 200:
        raise invalid('link is too long (200 characters at most).')

    text = f'{title}\n\n{message}'
    notification = Notification.objects.create(user=user, message=f'{title}: {message}'[:255], link=link or None)
    notify('volunteer_message', user, {'message': text, 'message_html': text_to_html(text), 'link': link})
    return {'sent': True, 'notification_id': str(notification.pk)}


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

def role_find(connection, params, request):
    roles = _roles().order_by('name', 'pk')
    if 'name' in params:
        roles = roles.filter(name__icontains=params['name'].strip())
    return _listed(roles, lambda role: {'role_id': str(role.pk), 'name': role.name, 'permanent': role.permanent,
                                        'description': role.description or ''},
                   lambda some: {'names': _joined(role.name for role in some)},
                   'roles', lambda some: [{'role_id': str(role.pk), 'name': role.name, 'permanent': role.permanent}
                                          for role in some])


def _assign(params, assign):
    """Console > Volunteers > Permanent roles, one role at a time."""
    user, role = _need_person(params), _permanent_role(params['role'])
    if assign and not user.is_active:
        raise refused(f"{events.person_name(user)}'s account is deactivated, so they can't be given a role.")
    roles = user.profile.permanent_roles
    changed = roles.filter(pk=role.pk).exists() != assign
    if changed:
        (roles.add if assign else roles.remove)(role)
    return {'person_id': str(user.pk), 'role_id': str(role.pk), 'role': role.name, 'assigned': assign, 'changed': changed,
            'permanent_roles': _joined(roles.order_by('name').values_list('name', flat=True))}


def role_assign(connection, params, request):
    return _assign(params, True)


def role_unassign(connection, params, request):
    return _assign(params, False)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def event_get(connection, params, request):
    event = _by_id(_events(), params['event_id'])
    return {'found': True, **event_output(event, request), **_event_lists(event)} if event else {'found': False}


def event_find(connection, params, request):
    now = timezone.now()
    found = _events()
    if not params.get('include_hidden', False):
        found = found.filter(published=True)
    within = timedelta(days=params['within_days']) if 'within_days' in params else None
    if params.get('when', 'upcoming') == 'past':
        found = found.filter(end_date__lt=now).order_by('-start_date', 'pk')
        if within:
            found = found.filter(end_date__gte=now - within)
    else:
        found = found.filter(end_date__gte=now).order_by('start_date', 'pk')
        if within:
            found = found.filter(start_date__lte=now + within)
    if 'query' in params:
        found = found.filter(title__icontains=params['query'].strip())
    return _listed(found, lambda event: {**event_output(event, request), **_event_lists(event)},
                   lambda some: {'titles': _joined(event.title for event in some)},
                   'events', lambda some: _event_items(some, request))


def _translated(instance, name, value):
    """Write a translated field in the organization's default language (as the AI connector does)."""
    from console.utils import lang_field
    setattr(instance, lang_field(name, settings.LANGUAGE_CODE), value)
    setattr(instance, name, value)


def event_create(connection, params, request):
    """A new event, always hidden from volunteers: staff (or `event.publish`) decide when it shows."""
    from base.models import Venue
    from events.models import Event

    title = ' '.join(params['title'].split())
    if params['ends_at'] <= params['starts_at']:
        raise invalid('The event must end after it starts: ends_at is not later than starts_at.')
    coordinator = _need_person(params, 'coordinator_id') if 'coordinator_id' in params else connection.created_by
    if 'coordinator_id' in params and not coordinator.is_active:
        raise refused(f"{events.person_name(coordinator)}'s account is deactivated, so they can't coordinate an event.")
    event = Event(published=False, start_date=params['starts_at'], end_date=params['ends_at'],
                  attendees=params.get('attendees', 0))
    _translated(event, 'title', title)
    _translated(event, 'description', params.get('description', '').strip())
    if 'venue' in params:
        event.venue = _need_named(Venue.objects, params['venue'], 'venue')
    elif 'place' in params:
        _translated(event, 'location', ' '.join(params['place'].split()))  # Event.save() looks its coordinates up
    event.save()
    if coordinator and coordinator.is_active:
        event.coordinators.add(coordinator)
    return {**event_output(event, request), 'created': True}


def event_update(connection, params, request):
    event = _need_event(params)
    given = [key for key in ('title', 'description', 'starts_at', 'ends_at', 'attendees') if key in params]
    if not given:
        raise invalid('Fill in at least one of title, description, starts_at, ends_at or attendees.')
    start, end = params.get('starts_at', event.start_date), params.get('ends_at', event.end_date)
    if end <= start:
        raise invalid('The event must end after it starts.')
    changed = []
    for key in given:
        if key in ('title', 'description'):
            value = ' '.join(params[key].split()) if key == 'title' else params[key].strip()
            if getattr(event, key) != value:
                _translated(event, key, value)
                changed.append(key)
        else:
            field = {'starts_at': 'start_date', 'ends_at': 'end_date', 'attendees': 'attendees'}[key]
            if getattr(event, field) != params[key]:
                setattr(event, field, params[key])
                changed.append(key)
    if changed:
        event.save()
    return {**event_output(event, request), 'updated': bool(changed), 'changed': ', '.join(changed)}


def _set_published(params, request, published):
    """Shows or hides the event. Town Hall's own Publish button also announces it (email
    campaign and push); a workflow that wants that sends its own message."""
    event = _need_event(params)
    changed = event.published != published
    if changed:
        event.published = published
        event.save(update_fields=['published'])
    return {'event_id': str(event.pk), 'title': event.title, 'published': event.published, 'changed': changed,
            'url': _event_url(request, event)}


def event_publish(connection, params, request):
    return _set_published(params, request, True)


def event_unpublish(connection, params, request):
    return _set_published(params, request, False)


def event_add_shift(connection, params, request):
    """A role slot, as the event's Roles & Staffing tab adds one."""
    from events.models import EventRoleSlot
    event = _need_event(params)
    role = _need_named(_roles(), params['role'], 'role')
    if role.permanent:
        raise invalid(f'"{role.name}" is a permanent position, not an event role.')
    if params['ends_at'] <= params['starts_at']:
        raise invalid('The shift must end after it starts: ends_at is not later than starts_at.')
    if event.is_past:
        raise refused(f'{event.title} is over, so shifts can no longer be added to it.')
    slot = EventRoleSlot.objects.create(
        event=event, role=role, start_time=params['starts_at'], end_time=params['ends_at'],
        required_qty=params.get('needed', 1), allowed_overstaffing_qty=params.get('extra', 0),
        is_public=not params.get('invite_only', False))
    return {**slot_output(slot), 'created': True}


def event_notify_volunteers(connection, params, request):
    """The event's "Message volunteers" in the console: a notification each, plus email and push."""
    from base.email import notify, text_to_html
    from base.models import Notification
    event = _need_event(params)
    text = params['message'].strip()
    link = _event_url(request, event)
    volunteers = list(event.volunteers().filter(is_active=True))
    Notification.objects.bulk_create([
        Notification(user=volunteer, message=f'{event.title}: {text}'[:255], link=link) for volunteer in volunteers])
    for volunteer in volunteers:
        notify('event_message', volunteer, {
            'event': event.title, 'message': text, 'message_html': text_to_html(text), 'link': link})
    return {'sent': len(volunteers), 'event_id': str(event.pk), 'title': event.title}


def event_find_absent(connection, params, request):
    """Who is signed up for a shift that is on right now and has no check-in for it (no `Shift`
    row for that slot, the same thing the no-show rule looks at once the shift is over)."""
    from jobs.models import Shift
    event = _by_id(_events(), params['event_id'])
    if not event:
        return _found('people', [])
    now = timezone.now()
    slots = {slot.pk: slot for slot in event.role_slots.select_related('role').filter(
        start_time__lte=now - timedelta(minutes=params.get('minutes_late', 0)), end_time__gt=now)}
    arrived = set(Shift.objects.filter(event_role_slot__in=list(slots)).values_list('event_role_slot_id', 'user_id'))
    signups = (events.signups().filter(eventroleslot__in=list(slots)).select_related('user')
               .order_by('eventroleslot__start_time', 'user__first_name', 'user__last_name', 'user_id', 'eventroleslot_id'))
    people = []
    for signup in signups:
        if (signup.eventroleslot_id, signup.user_id) not in arrived:
            slot = slots[signup.eventroleslot_id]
            people.append({
                **events.person_item(signup.user), 'role': slot.role.name, 'shift_id': str(slot.pk),
                'starts_at': events.iso(slot.start_time), 'ends_at': events.iso(slot.end_time),
                'minutes_late': int((now - slot.start_time).total_seconds() // 60),
            })
    return _found('people', people[:LIST_LIMIT], len(people), event_id=str(event.pk), title=event.title)


# ---------------------------------------------------------------------------
# Shifts at events
# ---------------------------------------------------------------------------

def shift_get(connection, params, request):
    from jobs.models import Shift
    slot = _by_id(_slots(), params['shift_id'])
    if not slot:
        return {'found': False}
    people = list(slot.signups.order_by(*events.BY_NAME))
    arrived = set(Shift.objects.filter(event_role_slot=slot).values_list('user_id', flat=True))
    return {'found': True, **slot_output(slot, people),
            'people': [{**events.person_item(user), 'checked_in': user.pk in arrived} for user in people[:LIST_LIMIT]]}


def shift_find(connection, params, request):
    event = _by_id(_events(), params['event_id'])
    if not event:
        return {'found': False, 'count': 0, 'shifts': []}
    slots = _slots().filter(event=event).exclude(role__system_key__isnull=False).order_by('start_time', 'pk')
    if 'role' in params:
        slots = slots.filter(role=_need_named(_roles(), params['role'], 'role'))
    if params.get('open_only', False):
        slots = slots.annotate(taken=Count('signups')).filter(taken__lt=F('required_qty'), end_time__gt=timezone.now())
    return _listed(slots, slot_output, lambda some: {}, 'shifts', events.shift_items)


def invite_find_pending(connection, params, request):
    """Invitations nobody answered: accepting marks one, declining deletes it, so what is left
    unaccepted for a shift that hasn't ended is still waiting."""
    from events.models import EventSlotInvite
    now = timezone.now()
    invites = (EventSlotInvite.objects.filter(accepted=False, user__isnull=False, event_role_slot__end_time__gt=now)
               .select_related('user', 'event_role_slot__role', 'event_role_slot__event').order_by('sent_at', 'pk'))
    if 'shift_id' in params:
        invites = invites.filter(event_role_slot=_need_slot(params))
    if 'event_id' in params:
        invites = invites.filter(event_role_slot__event=_need_event(params))
    if params.get('older_than_days'):
        invites = invites.filter(sent_at__lte=now - timedelta(days=params['older_than_days']))
    some, count = events.capped(invites)
    return _found('invites', [{
        **events.person_item(invite.user), 'invite_id': str(invite.pk), 'invited_at': events.iso(invite.sent_at),
        'shift_id': str(invite.event_role_slot_id), 'role': invite.event_role_slot.role.name,
        'starts_at': events.iso(invite.event_role_slot.start_time),
        'event_id': str(invite.event_role_slot.event_id), 'event_title': invite.event_role_slot.event.title,
    } for invite in some], count)


def shift_sign_up(connection, params, request):
    """What a volunteer's own sign-up checks (events.views.role_slot_signup and
    respond_to_invite), in the same order, then `EventRoleSlot.add_signup()` and the same
    confirmation. There is no staff override here: a workflow gets no further than the person would."""
    from base.email import notify
    from base.utils import short_datetime
    from events.models import EventSlotInvite

    user, slot = _need_person(params), _need_slot(params)
    event, role, name = slot.event, slot.role, events.person_name(user)
    if slot.user_signed_up(user):
        return {**slot_brief(slot), 'signed_up': True, 'created': False}
    if not user.is_active:
        raise refused(f"{name}'s account is deactivated, so they can't be signed up.")
    if not event.published:
        raise refused(f'{event.title} is hidden from volunteers. Publish it before signing people up.')
    invite = EventSlotInvite.objects.filter(event_role_slot=slot, user=user, accepted=False).first()
    if not slot.is_public and not invite:
        raise refused(f'This {role.name} shift is by invitation only, and {name} has no open invitation to it. Invite them first.')
    if slot.has_ended:
        raise refused('This shift has already ended.')
    if not role.has_user_completed_required_training(user):
        raise refused(f"{name} hasn't completed the training {role.name} requires.")
    if not role.has_user_required_certificates(user):
        raise refused(f"{name} doesn't have the verified certificates {role.name} requires.")
    if (slot.overstaffing_slots_left() <= 0) if invite else slot.is_fully_staffed():
        raise refused(f'This {role.name} shift is already full.')
    if slot.user_has_conflict(user):
        raise refused(f'{name} is signed up for another shift at the same time.')

    slot.add_signup(user)
    if invite:
        invite.accepted = True
        invite.save(update_fields=['accepted'])
    language = user.profile.language if user.profile.language in dict(settings.LANGUAGES) else None
    notify('signup_confirmed', user, {
        'event': event.title, 'role': role.name, 'date': short_datetime(slot.start_time, language),
        'location': event.full_location + (f' ({event.report_to_location})' if event.report_to_location else ''),
        'link': _event_url(request, event),
    })
    return {**slot_brief(slot), 'signed_up': True, 'created': True}


def shift_cancel(connection, params, request):
    """The volunteer's own "Cancel my shift" (events/reliability.py): the cancellation is
    recorded, and a late one counts against their reliability and reaches the coordinators."""
    from events import reliability
    user, slot = _need_person(params), _need_slot(params)
    if not slot.user_signed_up(user):
        return {**slot_brief(slot), 'cancelled': False, 'late': False}
    if not reliability.cancellable([slot], user):
        if slot.is_area_lead:
            raise refused('Area lead shifts are assigned by the coordinators, in Town Hall.')
        raise refused("That shift has already started or ended, so it can't be cancelled.")
    cancellation = reliability.cancel_signup(slot, user)
    return {**slot_brief(slot), 'cancelled': True, 'late': cancellation.late}


def shift_invite(connection, params, request):
    """One invitation, sent the way the console and the AI connector send them
    (console.views.events.send_invites): the person decides."""
    from base.utils import short_datetime
    from console.views.events import send_invites
    from events.models import EventSlotInvite

    user, slot = _need_person(params), _need_slot(params)
    event, role = slot.event, slot.role
    if event.is_past:
        raise refused(f'{event.title} is over.')
    if slot.is_area_lead:
        raise refused('Area lead shifts are assigned by the coordinators, in Town Hall.')
    if not user.is_active:
        raise refused(f"{events.person_name(user)}'s account is deactivated, so they can't be invited.")
    if slot.user_signed_up(user):
        return {**slot_brief(slot), 'invited': False, 'invite_id': ''}
    body = params.get('message', '').strip() or _(
        "Hello [Name],\n\nYour skills would make a real difference at %(event)s. "
        "We're looking for a %(role)s on %(date)s and thought of you.\n\nWe'd love to have you on the team!"
    ) % {'event': event.title, 'role': role.name, 'date': short_datetime(slot.start_time)}
    send_invites(slot, [user], _('You are invited to %(event)s') % {'event': event.title}, body, request.build_absolute_uri)
    invite = EventSlotInvite.objects.filter(event_role_slot=slot, user=user, accepted=False).first()
    return {**slot_brief(slot), 'invited': True, 'invite_id': str(invite.pk) if invite else ''}


def _started(shift, started):
    slot = shift.event_role_slot if shift.event_role_slot_id else None
    return {
        'started': started, 'role': shift.role.name if shift.role_id else '', 'started_at': events.iso(shift.start_time),
        'shift_id': str(slot.pk) if slot else '',
        'event_id': str(slot.event_id) if slot else '', 'event_title': slot.event.title if slot else '',
    }


def shift_start(connection, params, request):
    """A check-in. At an event it is the kiosk's rule for the volunteer's own shift (signed up,
    from an hour before it starts until it ends); in a permanent role it is the API's (assigned
    to it). One shift at a time, as everywhere."""
    from base.models import Profile
    from jobs.models import Shift
    from jobs.views import EARLY_CHECK_IN

    user = _need_person(params)
    name = events.person_name(user)
    if ('shift_id' in params) == ('role' in params):
        raise invalid('Give either shift_id (a shift at an event) or role (a permanent role), not both.')
    if not user.is_active:
        raise refused(f"{name}'s account is deactivated, so they can't be checked in.")
    slot = _need_slot(params) if 'shift_id' in params else None
    role = None if slot else _permanent_role(params['role'])
    Profile.objects.select_for_update().filter(user=user).first()  # two calls at once can't start two shifts
    running = _open_shift(user)
    if running:
        same = running.event_role_slot_id == slot.pk if slot else (running.role_id == role.pk and not running.event_role_slot_id)
        if same:
            return _started(running, False)
        raise refused(f'{name} is already checked in as {running.role.name if running.role_id else "something else"}. Check them out first.')
    if slot:
        now = timezone.now()
        if not slot.user_signed_up(user):
            raise refused(f'{name} is not signed up for this {slot.role.name} shift.')
        if slot.end_time < now:
            raise refused('This shift has already ended.')
        if slot.start_time > now + EARLY_CHECK_IN:
            raise refused('Check-in opens an hour before the shift starts.')
        return _started(Shift.objects.create(user=user, event_role_slot=slot), True)
    if not role.is_assigned_to(user):
        raise refused(f'{name} is not assigned to the permanent role {role.name}. Assign it first.')
    return _started(Shift.objects.create(user=user, role=role), True)


def shift_end(connection, params, request):
    """A check-out, as at the kiosk: the shift ends now, its points are worked out, and after
    an event shift the thank-you goes out."""
    from api.models import ShiftHeartbeat
    from events.reminders import send_shift_thanks

    user = _need_person(params)
    shift = _open_shift(user)
    if not shift:
        return {'ended': False}
    ShiftHeartbeat.objects.filter(shift=shift).delete()  # a shift another app keeps alive is over now
    shift.end_shift()
    slot = shift.event_role_slot if shift.event_role_slot_id else None
    if slot:
        send_shift_thanks(shift, request.build_absolute_uri('/'))
    return {
        'ended': True, 'role': shift.role.name if shift.role_id else '',
        'started_at': events.iso(shift.start_time), 'ended_at': events.iso(shift.end_time),
        'hours': round(shift.duration(), 2), 'points': shift.end_impact_points or 0,
        'event_id': str(slot.event_id) if slot else '', 'event_title': slot.event.title if slot else '',
    }


# ---------------------------------------------------------------------------
# Training and venues
# ---------------------------------------------------------------------------

def training_find(connection, params, request):
    from education.models import TrainingModule
    modules = TrainingModule.objects.filter(staff_only=False).order_by('title', 'pk')
    if 'title' in params:
        modules = modules.filter(title__icontains=params['title'].strip())
    return _listed(modules, lambda module: {
        'module_id': str(module.pk), 'title': module.title, 'published': module.published,
        'minutes': module.get_total_length(),
        'url': request.build_absolute_uri(reverse('module_overview', args=[module.pk])),
    }, lambda some: {'titles': _joined(module.title for module in some)},
        'modules', lambda some: [{
            'module_id': str(module.pk), 'title': module.title, 'published': module.published,
            'url': request.build_absolute_uri(reverse('module_overview', args=[module.pk])),
        } for module in some])


def training_find_expiring(connection, params, request):
    """Completions of modules that have to be renewed (`expires_after_days`) whose expiry falls
    in the coming days: one query per such module, like the `training.expired` event."""
    from education.models import TrainingModule, TrainingModuleCompletion
    now, ahead = timezone.now(), timedelta(days=params.get('days', 30))
    modules = TrainingModule.objects.filter(staff_only=False, expires_after_days__gt=0)
    if 'module' in params:
        modules = [_need_named(TrainingModule.objects.filter(staff_only=False), params['module'], 'training module', 'title')]
    people, count = [], 0
    for module in modules:
        if not module.expires_after_days:
            continue
        lasts = timedelta(days=module.expires_after_days)
        # Expires after now and by now + ahead; the ones expiring soonest were completed first.
        some, total = events.capped(TrainingModuleCompletion.objects.filter(
            training_module=module, user__is_active=True, completed_at__gt=now - lasts, completed_at__lte=now + ahead - lasts,
        ).select_related('user').order_by('completed_at', 'pk'))
        count += total
        for completion in some:
            expires_at = completion.completed_at + lasts
            people.append((expires_at, completion.pk, {
                **events.person_item(completion.user), 'module_id': str(module.pk), 'module_title': module.title,
                'completed_at': events.iso(completion.completed_at), 'expires_at': events.iso(expires_at),
                'days_left': _days_left(expires_at, now),
            }))
    people.sort(key=lambda row: row[:2])
    return _found('people', [item for _expires_at, _pk, item in people[:LIST_LIMIT]], count)


def certificate_find_expiring(connection, params, request):
    """Verified certificates of active people that run out in the coming days (what the
    `certificate.expiring` event reports 30 days ahead, for any number of days)."""
    from education.models import ExternalCertificate, UserCertification
    now = timezone.now()
    held = UserCertification.objects.filter(
        verified=True, user__is_active=True, expiration_date__gt=now,
        expiration_date__lte=now + timedelta(days=params.get('days', 30)),
    ).select_related('user', 'certificate').order_by('expiration_date', 'pk')
    if 'certificate' in params:
        held = held.filter(certificate=_need_named(ExternalCertificate.objects, params['certificate'], 'certificate'))
    some, count = events.capped(held)
    return _found('people', [{
        **events.person_item(one.user), 'certificate_id': str(one.certificate_id), 'certificate': one.certificate.name,
        'expires_at': events.iso(one.expiration_date), 'days_left': _days_left(one.expiration_date, now),
    } for one in some], count)


def venue_find(connection, params, request):
    from base.models import Venue
    venues = Venue.objects.order_by('name', 'pk')
    if 'name' in params:
        venues = venues.filter(name__icontains=params['name'].strip())
    return _listed(venues, lambda venue: {
        'venue_id': str(venue.pk), 'name': venue.name, 'address': venue.address or '', 'phone': venue.phone_number or '',
        'url': request.build_absolute_uri(reverse('venue_detail', args=[venue.pk])),
    }, lambda some: {'names': _joined(venue.name for venue in some)},
        'venues', lambda some: [{
            'venue_id': str(venue.pk), 'name': venue.name, 'address': venue.address or '', 'phone': venue.phone_number or '',
            'url': request.build_absolute_uri(reverse('venue_detail', args=[venue.pk])),
        } for venue in some])


def venue_post_note(connection, params, request):
    """A notice on a venue, as the venue editor posts one."""
    from base.models import Venue, VenueNote
    venue = _need_named(Venue.objects, params['venue'], 'venue')
    note = VenueNote.objects.create(venue=venue, text=params['note'].strip(), expires_after=timedelta(days=params.get('days', 7)))
    return {'note_id': str(note.pk), 'venue_id': str(venue.pk), 'venue': venue.name, 'expires_at': events.iso(note.expires_at)}


HANDLERS = {
    'person.find': person_find,
    'person.get': person_get,
    'person.search': person_search,
    'person.next_shift': person_next_shift,
    'shift.current': shift_current,
    'role.check': role_check,
    'training.status': training_status,
    'certificate.status': certificate_status,
    'person.invite': person_invite,
    'person.update': person_update,
    'person.deactivate': person_deactivate,
    'person.reactivate': person_reactivate,
    'points.award': points_award,
    'endorsement.give': endorsement_give,
    'notification.send': notification_send,
    'role.find': role_find,
    'role.assign': role_assign,
    'role.unassign': role_unassign,
    'event.get': event_get,
    'event.find': event_find,
    'event.create': event_create,
    'event.update': event_update,
    'event.publish': event_publish,
    'event.unpublish': event_unpublish,
    'event.add_shift': event_add_shift,
    'event.notify_volunteers': event_notify_volunteers,
    'event.find_absent': event_find_absent,
    'shift.get': shift_get,
    'shift.find': shift_find,
    'invite.find_pending': invite_find_pending,
    'shift.sign_up': shift_sign_up,
    'shift.cancel': shift_cancel,
    'shift.invite': shift_invite,
    'shift.start': shift_start,
    'shift.end': shift_end,
    'training.find': training_find,
    'training.find_expiring': training_find_expiring,
    'certificate.find_expiring': certificate_find_expiring,
    'venue.find': venue_find,
    'venue.post_note': venue_post_note,
}
