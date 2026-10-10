"""Events: reading, creating and editing them (never deleting), their role slots and
areas, and staffing them through invitations."""
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.urls import reverse
from django.utils import timezone

from base.models import Venue
from events import leadership
from events.models import Event, EventArea, EventCategory, EventRoleSlot
from jobs.models import Role

from . import (
    ToolError, array, boolean, get_or_error, integer, local, obj, parse_time, person, set_translated, string, text, time, tool,
)

THIN_DESCRIPTION = 250  # characters


def slot_summary(slot):
    return {
        'slot_id': slot.pk, 'role': slot.role.name, 'role_id': slot.role_id,
        'start': local(slot.start_time), 'end': local(slot.end_time),
        'required': slot.required_qty, 'extra_allowed': slot.allowed_overstaffing_qty,
        'signed_up': slot.signups.count(), 'pending_invites': slot.invites.filter(accepted=False).count(),
        'area_id': slot.area_id, 'area': slot.area.name if slot.area_id else None,
        'invite_only': not slot.is_public,
        'shift_lead': person(slot.lead) if slot.lead_id else None,
    }


def event_summary(event):
    return {
        'event_id': event.pk, 'title': event.title,
        'start': local(event.start_date), 'end': local(event.end_date),
        'place': event.place_name or None, 'published': event.published,
        'status': 'past' if event.is_past else 'live' if event.is_live else 'upcoming',
    }


def console_url(ctx, event):
    return ctx.base_url + reverse('console_event_edit', args=[event.pk])


def _event(event_id):
    return get_or_error(Event.objects.select_related('venue'), event_id, 'event')


def missing_for(event, slots):
    missing = []
    description = event.description or ''
    if not description.strip():
        missing.append('description (none yet)')
    elif len(description) < THIN_DESCRIPTION:
        missing.append(f'description is short ({len(description)} characters)')
    if not event.venue_id and not event.location:
        missing.append('venue or address')
    if not event.attendees:
        missing.append('expected attendees')
    if EventCategory.objects.exists() and not event.category.exists():
        missing.append('categories')
    if not [s for s in slots if not s.is_area_lead]:
        missing.append('volunteer roles and shifts')
    if not event.image:
        missing.append('header image (uploaded in Town Hall\'s event editor)')
    if not event.published:
        missing.append('not published yet (volunteers can\'t see it)')
    return missing


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

@tool('list_events', 'List events',
      'Events in Town Hall, newest first for past events and soonest first otherwise. Use get_event for the details of one.',
      {
          'when': string('Which events', enum=['upcoming', 'live', 'past', 'all']),
          'query': string('Optional words to match in the title, description, place or category'),
          'limit': integer('At most 50 (default 20)', minimum=1),
      }, read_only=True)
def list_events(ctx, when='upcoming', query='', limit=20):
    now = timezone.now()
    events = Event.objects.select_related('venue')
    if when == 'upcoming':
        events = events.filter(start_date__gt=now).order_by('start_date')
    elif when == 'live':
        events = events.filter(start_date__lte=now, end_date__gte=now).order_by('start_date')
    elif when == 'past':
        events = events.filter(end_date__lt=now).order_by('-start_date')
    else:
        events = events.order_by('-start_date')
    for word in text(query).split():
        events = events.filter(Q(title__icontains=word) | Q(description__icontains=word) | Q(location__icontains=word)
                               | Q(venue__name__icontains=word) | Q(category__name__icontains=word))
    events = events.distinct()[:min(limit, 50)]
    return [event_summary(e) for e in events] or 'No events match.'


@tool('get_event', 'Get event details',
      'Everything about one event: details, coordinators, role slots with staffing, areas (chain of command), '
      'and a checklist of what is still missing.',
      {'event_id': integer('The event')}, ['event_id'], read_only=True)
def get_event(ctx, event_id):
    event = _event(event_id)
    slots = list(event.role_slots.select_related('role', 'area', 'lead').order_by('start_time'))
    work = [s for s in slots if not s.is_area_lead]
    return {
        **event_summary(event),
        'description': event.description,
        'venue': {'venue_id': event.venue_id, 'name': event.venue.name, 'address': event.venue.address} if event.venue_id else None,
        'address': event.location or None,
        'check_in_point': event.report_to_location or None,
        'expected_attendees': event.attendees,
        'featured': event.featured,
        'categories': list(event.category.values('id', 'name')),
        'coordinators': [person(u) for u in event.coordinators.all()],
        'chain_of_command': event.chain_of_command,
        'chain_of_command_suggested': leadership.suggests_chain(event),
        'areas': [{'area_id': a.pk, 'name': a.name, 'icon': a.icon} for a in event.areas.all()] if event.chain_of_command else [],
        'role_slots': [slot_summary(s) for s in work],
        'area_lead_shifts': [slot_summary(s) for s in slots if s.is_area_lead],
        'volunteers_needed': sum(s.required_qty for s in work),
        'volunteers_signed_up': sum(s.signups.count() for s in work),
        'post_event_statement': event.post_event_statement or None,
        'missing': missing_for(event, slots),
        'console_url': console_url(ctx, event),
    }


@tool('list_venues', 'List venues', "The organization's saved venues, for an event's venue_id.", read_only=True)
def list_venues(ctx):
    return [{'venue_id': v.pk, 'name': v.name, 'address': v.address} for v in Venue.objects.order_by('name')] or 'No saved venues.'


@tool('list_event_categories', 'List event categories', 'Categories an event can be filed under.', read_only=True)
def list_event_categories(ctx):
    return [{'category_id': c.pk, 'name': c.name} for c in EventCategory.objects.order_by('name')] or 'No categories.'


@tool('find_similar_events', 'Find similar past events',
      'Search other events by keywords to learn from what the organization did before: the roles and headcounts used '
      '(also per 100 attendees, to estimate how many of each role a new event needs), how well they filled, hours '
      'worked, and volunteer feedback.',
      {
          'query': string('A few keywords; an event matches if any of them appear'),
          'exclude_event_id': integer('An event to leave out, usually the one being planned'),
          'limit': integer('At most 10 (default 5)', minimum=1),
      }, ['query'], read_only=True)
def find_similar_events(ctx, query, exclude_event_id=None, limit=5):
    words = [w for w in query.replace(',', ' ').split() if len(w) > 2][:8]
    if not words:
        raise ToolError('Give a few keywords of at least three letters.')
    match = Q()
    for word in words:
        match |= Q(title__icontains=word) | Q(description__icontains=word) | Q(category__name__icontains=word)
    events = (Event.objects.filter(match).exclude(pk=exclude_event_id).distinct()
              .annotate(volunteer_count=Count('role_slots__signups', distinct=True))
              .order_by('-start_date')[:min(limit, 10)])
    results = []
    for event in events:
        roles = {}
        for slot in event.role_slots.select_related('role').exclude(role__system_key='area_lead'):
            entry = roles.setdefault(slot.role.name, {'role_id': slot.role_id, 'slots': 0, 'required': 0, 'signed_up': 0})
            entry['slots'] += 1
            entry['required'] += slot.required_qty
            entry['signed_up'] += slot.signups.count()
        if event.attendees:
            for entry in roles.values():
                entry['required_per_100_attendees'] = round(entry['required'] / event.attendees * 100, 1)
        feedback = event.feedback.all()
        rating = feedback.aggregate(avg=Avg('rating'))['avg']
        results.append({
            **event_summary(event),
            'description': (event.description or '')[:600],
            'categories': list(event.category.values_list('name', flat=True)),
            'expected_attendees': event.attendees,
            'volunteers': event.volunteer_count,
            'hours_logged': event.hours_logged if event.is_past else None,
            'roles': roles,
            'feedback': {
                'responses': feedback.count(), 'average_rating': round(rating, 1) if rating else None,
                'suggestions': [f.suggestions[:300] for f in feedback.exclude(suggestions='')[:5]],
            },
            'post_event_statement': (event.post_event_statement or '')[:400] or None,
        })
    return results or f'No other events match {query!r}.'


# ---------------------------------------------------------------------------
# Creating and editing
# ---------------------------------------------------------------------------

EVENT_FIELDS = {
    'title': string('At most 100 characters', maxLength=100),
    'description': string('What the event is, who it is for and what will happen. Plain text; blank lines separate paragraphs'),
    'start': time('When it starts'),
    'end': time('When it ends'),
    'venue_id': integer('A venue from list_venues, or 0 for a one-off address', minimum=0),
    'address': string('One-off street address, used when there is no venue', maxLength=200),
    'check_in_point': string('Where volunteers report on arrival, e.g. "Side door by the parking lot"', maxLength=200),
    'expected_attendees': integer('Expected number of attendees', minimum=0),
    'category_ids': array(integer('Category id'), 'Replaces the categories (ids from list_event_categories)'),
    'coordinator_ids': array(integer('User id'), 'Replaces the general coordinators (people who run the event)'),
}


def _apply(event, args):
    """Copy tool arguments onto the event. Returns (changed field labels, after-save callbacks)."""
    changed, later = [], []
    if 'title' in args:
        title = text(args['title'], 100)
        if not title:
            raise ToolError('The title cannot be empty.')
        set_translated(event, 'title', title)
        changed.append('title')
    if 'description' in args:
        set_translated(event, 'description', text(args['description']))
        changed.append('description')
    start = parse_time(args['start'], 'start') if args.get('start') else event.start_date
    end = parse_time(args['end'], 'end') if args.get('end') else event.end_date
    if start and end and end <= start:
        raise ToolError('The event must end after it starts.')
    if args.get('start'):
        event.start_date = start
        changed.append('start')
    if args.get('end'):
        event.end_date = end
        changed.append('end')
    if 'venue_id' in args:
        event.venue = get_or_error(Venue.objects, args['venue_id'], 'venue') if args['venue_id'] else None
        changed.append('venue')
    if 'address' in args:
        set_translated(event, 'location', text(args['address'], 200))
        event.latitude = event.longitude = None  # geocoded again on save
        changed.append('address')
    if 'check_in_point' in args:
        event.report_to_location = text(args['check_in_point'], 200)
        changed.append('check-in point')
    if 'expected_attendees' in args:
        event.attendees = args['expected_attendees']
        changed.append('expected attendees')
    for flag in ('published', 'featured'):
        if flag in args:
            setattr(event, flag, args[flag])
            changed.append(flag)
    if 'category_ids' in args:
        categories = list(EventCategory.objects.filter(pk__in=args['category_ids']))
        if len(categories) != len(set(args['category_ids'])):
            raise ToolError('Some category ids do not exist; see list_event_categories.')
        later.append(lambda e: e.category.set(categories))
        changed.append('categories')
    if 'coordinator_ids' in args:
        people = list(User.objects.filter(pk__in=args['coordinator_ids'], is_active=True))
        if len(people) != len(set(args['coordinator_ids'])):
            raise ToolError('Some coordinator ids are unknown or inactive; see search_people.')
        later.append(lambda e: e.coordinators.set(people))
        changed.append('coordinators')
    return changed, later


@tool('create_event', 'Create an event',
      'Create a new event. It starts unpublished (hidden from volunteers) unless published is true; you become a general '
      'coordinator unless coordinator_ids says otherwise. Add roles afterwards with add_role_slots.',
      {**EVENT_FIELDS, 'published': boolean('Show it to volunteers now (default false)')},
      ['title', 'start', 'end'])
def create_event(ctx, **args):
    event = Event(published=False, description='')
    with transaction.atomic():
        _changed, later = _apply(event, args)
        event.save()
        if 'coordinator_ids' not in args:
            event.coordinators.add(ctx.user)
        for apply in later:
            apply(event)
    return {'created': event_summary(event), 'console_url': console_url(ctx, event),
            'missing': missing_for(event, [])}


@tool('update_event', 'Edit an event',
      'Change an event\'s details. Pass only what changes. Title and description are written in the organization\'s '
      'default language; other languages can be filled with Auto-translate in Town Hall. Events cannot be deleted here.',
      {'event_id': integer('The event'), **EVENT_FIELDS,
       'published': boolean('Visible to volunteers'), 'featured': boolean('Highlighted on the home page')},
      ['event_id'], idempotent=True)
def update_event(ctx, event_id, **args):
    event = _event(event_id)
    if not args:
        raise ToolError('Nothing to change.')
    with transaction.atomic():
        changed, later = _apply(event, args)
        event.save()
        for apply in later:
            apply(event)
    return {'updated': changed, 'event': event_summary(event)}


# ---------------------------------------------------------------------------
# Role slots and areas
# ---------------------------------------------------------------------------

SLOT = obj({
    'start': time('Shift start'),
    'end': time('Shift end'),
    'required': integer('Volunteers needed', minimum=1),
    'extra': integer('Extra volunteers allowed beyond that (default 0)', minimum=0),
}, ['start', 'end', 'required'])


def _area(event, area_id):
    if not area_id:
        return None
    if not event.chain_of_command:
        raise ToolError('This event has no chain of command, so it has no areas. Use enable_chain_of_command first.')
    return get_or_error(event.areas, area_id, 'area at this event')


@tool('add_role_slots', 'Add roles to an event',
      'Add time slots (shifts) for a role at an event. One call can add several shifts of the same role, e.g. a morning '
      'and an afternoon shift. Use list_roles for role ids and find_similar_events to judge headcounts.',
      {
          'event_id': integer('The event'),
          'role_id': integer('The role (from list_roles)'),
          'slots': array(SLOT, 'The shifts to add', minItems=1, maxItems=30),
          'area_id': integer('Area to put them in, for events with a chain of command'),
          'invite_only': boolean('Hide these shifts from the public listing; people join by invitation (default false)'),
      }, ['event_id', 'role_id', 'slots'])
def add_role_slots(ctx, event_id, role_id, slots, area_id=None, invite_only=False):
    event = _event(event_id)
    role = get_or_error(Role.objects.filter(system_key__isnull=True), role_id, 'role')
    if role.permanent:
        raise ToolError(f'"{role.name}" is a permanent staff position, not an event role.')
    area = _area(event, area_id)
    built = []
    for index, row in enumerate(slots, start=1):
        start, end = parse_time(row['start'], f'slot {index} start'), parse_time(row['end'], f'slot {index} end')
        if end <= start:
            raise ToolError(f'Slot {index} must end after it starts.')
        built.append(EventRoleSlot(event=event, role=role, area=area, start_time=start, end_time=end, is_public=not invite_only,
                                   required_qty=row['required'], allowed_overstaffing_qty=row.get('extra') or 0))
    suggested_before = leadership.suggests_chain(event)
    with transaction.atomic():
        for slot in built:
            slot.save()
    result = {'added': [slot_summary(s) for s in built]}
    if not suggested_before and leadership.suggests_chain(event):
        result['note'] = (f'The event now needs more than {leadership.SUGGEST_CHAIN_AT} volunteers. Consider a chain of '
                          'command (enable_chain_of_command, then create_area) so areas have leads.')
    return result


@tool('update_role_slot', 'Edit a role slot',
      'Change a shift\'s times, headcount, area or visibility. Pass only what changes. People already signed up are '
      'not notified automatically. Shifts cannot be removed here; that is done in Town Hall.',
      {
          'slot_id': integer('The slot'),
          'start': time('New start'),
          'end': time('New end'),
          'required': integer('Volunteers needed', minimum=1),
          'extra': integer('Extra volunteers allowed', minimum=0),
          'area_id': integer('Area to move it to, or 0 for none'),
          'invite_only': boolean('Hide from the public listing'),
      }, ['slot_id'], idempotent=True)
def update_role_slot(ctx, slot_id, **args):
    slot = get_or_error(EventRoleSlot.objects.select_related('event', 'role', 'area'), slot_id, 'slot')
    if slot.is_area_lead:
        raise ToolError('Area lead shifts are managed in Town Hall.')
    if not args:
        raise ToolError('Nothing to change.')
    if args.get('start'):
        slot.start_time = parse_time(args['start'], 'start')
    if args.get('end'):
        slot.end_time = parse_time(args['end'], 'end')
    if slot.end_time <= slot.start_time:
        raise ToolError('The shift must end after it starts.')
    if 'required' in args:
        slot.required_qty = args['required']
    if 'extra' in args:
        slot.allowed_overstaffing_qty = args['extra']
    if 'area_id' in args:
        slot.area = _area(slot.event, args['area_id'])
    if 'invite_only' in args:
        slot.is_public = not args['invite_only']
    slot.save()
    result = {'updated': slot_summary(slot)}
    signed_up = slot.signups.count()
    if signed_up and ('start' in args or 'end' in args):
        result['note'] = f'{signed_up} people are signed up for this shift; let them know about the new time.'
    if signed_up > slot.required_qty + slot.allowed_overstaffing_qty:
        result['note'] = f'{signed_up} people are already signed up, more than the shift now allows.'
    return result


@tool('enable_chain_of_command', 'Turn on chain of command',
      'Organise an event into areas (kitchen, stage, parking...) that each have area leads, which helps events needing '
      'more than 20 volunteers. Area leads are assigned by the event\'s coordinators in Town Hall. Turning it off is '
      'only possible in Town Hall.',
      {'event_id': integer('The event')}, ['event_id'], idempotent=True)
def enable_chain_of_command(ctx, event_id):
    event = _event(event_id)
    Event.objects.filter(pk=event.pk).update(chain_of_command=True)
    return 'Chain of command is on. Create areas with create_area, then put shifts in them (area_id).'


@tool('create_area', 'Create an event area',
      'Add an area to an event with a chain of command, e.g. "Kitchen" or "Kids zone".',
      {
          'event_id': integer('The event'),
          'name': string('Area name', maxLength=100),
          'icon': string('A Google Material Symbols icon name, e.g. restaurant'),
      }, ['event_id', 'name'])
def create_area(ctx, event_id, name, icon=''):
    event = _event(event_id)
    if not event.chain_of_command:
        raise ToolError('Turn on the chain of command first (enable_chain_of_command).')
    name = text(name, 100)
    if not name:
        raise ToolError('The area needs a name.')
    area = EventArea(event=event, order=event.areas.count(), icon=text(icon, 50))
    set_translated(area, 'name', name)
    area.save()
    return {'area_id': area.pk, 'name': area.name}


# ---------------------------------------------------------------------------
# Staffing through invitations
# ---------------------------------------------------------------------------

@tool('find_volunteers_for_slot', 'Find volunteers for a shift',
      'Volunteers ranked by fit for a shift\'s role (preferred skills, finished training, experience), with time '
      'conflicts and pending invitations. Volunteers who have not finished the role\'s required training cannot sign up.',
      {
          'slot_id': integer('The slot'),
          'query': string('Optional filter on name, email or skill'),
          'limit': integer('At most 30 (default 10)', minimum=1),
      }, ['slot_id'], read_only=True)
def find_volunteers_for_slot(ctx, slot_id, query='', limit=10):
    from console.views.events import _recipients
    slot = get_or_error(EventRoleSlot.objects.select_related('role', 'event'), slot_id, 'slot')
    ranked = _recipients(slot, text(query))[:min(limit, 30)]
    return [{
        **person(r['user']), 'fit_score': r['score'], 'recommended': r['recommended'],
        'skills': [s.name for s in r['matched']], 'impact_points': r['user'].profile.impact_points,
        'training_done': slot.role.has_user_completed_required_training(r['user']),
        'time_conflict': r['conflict'], 'already_invited': r['invited'],
    } for r in ranked] or 'Nobody matches.'


@tool('invite_volunteers', 'Invite volunteers to a shift',
      'Send invitations for a shift. Each person gets a notification and an email and decides whether to accept; '
      'nobody is signed up without agreeing. Confirm with the user who should be invited before calling this.',
      {
          'slot_id': integer('The slot'),
          'user_ids': array(integer('User id'), 'People to invite', minItems=1, maxItems=50),
          'message': string('Optional personal note; [Name] is replaced with each first name'),
      }, ['slot_id', 'user_ids'], open_world=True)
def invite_volunteers(ctx, slot_id, user_ids, message=''):
    from console.views.events import send_invites
    slot = get_or_error(EventRoleSlot.objects.select_related('role', 'event'), slot_id, 'slot')
    if slot.event.is_past:
        raise ToolError('This event is over.')
    users = list(User.objects.filter(pk__in=user_ids, is_active=True).exclude(commitments=slot))
    if not users:
        raise ToolError('None of those people can be invited (unknown, inactive or already signed up).')
    subject = f'You are invited to {slot.event.title}'
    body = text(message) or (
        f"Hello [Name],\n\nWe're looking for a {slot.role.name} at {slot.event.title} on {local(slot.start_time)} "
        "and thought of you.\n\nWe'd love to have you on the team!"
    )
    sent = send_invites(slot, users, subject, body, lambda path: ctx.base_url + path)
    return {'invited': [person(u) for u in users], 'count': sent}
