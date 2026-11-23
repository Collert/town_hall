"""What Town Hall offers Switchboard: the triggers it emits and the actions it runs.

Switchboard builds its nodes from this, and workflows store the `type` and `name` values, so
they must never be renamed. Titles and descriptions are shown to people in Switchboard, in
English (the protocol's language), so they aren't translated here.
"""
from django.templatetags.static import static

PROTOCOL = '1'
BUILDING = 'town-hall'
NAME = 'Town Hall'


def field(key, type_, label, example, **extra):
    return {'key': key, 'type': type_, 'label': label, 'example': example, **extra}


def param(key, type_, label, required=False, help=''):
    spec = {'key': key, 'type': type_, 'label': label, 'required': required}
    if help:
        spec['help'] = help
    return spec


PERSON = [
    field('person.id', 'string', 'Person ID', '42'),
    field('person.name', 'string', 'Name', 'Maria K.'),
    field('person.email', 'string', 'Email', 'maria@example.org'),
]
SLOT = [
    field('shift.id', 'string', 'Shift ID', '318'),
    field('shift.role', 'string', 'Role', 'Greeter'),
    field('shift.starts_at', 'datetime', 'Shift starts', '2026-12-06T09:00:00-08:00'),
    field('shift.ends_at', 'datetime', 'Shift ends', '2026-12-06T13:00:00-08:00'),
    field('event.id', 'string', 'Event ID', '17'),
    field('event.title', 'string', 'Event', 'Christmas Bazaar'),
    field('event.place', 'string', 'Place', 'Parish Hall'),
]

TRIGGERS = [
    {
        'type': 'volunteer.level_reached',
        'title': 'Volunteer reached a level',
        'description': 'Starts every time a volunteer levels up.',
        'summary': '{person.name} reached level {level}',
        'fields': PERSON + [
            field('level', 'int', 'Level', 3),
            field('level_name', 'string', 'Level name', 'Pillar'),
            field('previous_level', 'int', 'Previous level', 2),
            field('impact_points', 'int', 'Impact points', 1250),
        ],
    },
    {
        'type': 'volunteer.registered',
        'title': 'Volunteer registered',
        'description': 'Starts when someone creates an account, or staff add them.',
        'summary': '{person.name} registered',
        'fields': PERSON + [
            field('registered_at', 'datetime', 'Registered', '2026-11-19T16:20:04-08:00'),
        ],
    },
    {
        'type': 'shift.signed_up',
        'title': 'Volunteer signed up for a shift',
        'description': 'Starts when a volunteer signs up for a shift at an event, or accepts an invitation to one.',
        'summary': '{person.name} signed up as {shift.role} at {event.title}',
        'fields': PERSON + SLOT,
    },
    {
        'type': 'shift.cancelled',
        'title': 'Volunteer left a shift',
        'description': 'Starts when a volunteer cancels a shift they signed up for, or staff take them off it.',
        'summary': '{person.name} left {shift.role} at {event.title}',
        'fields': PERSON + SLOT,
    },
    {
        'type': 'shift.completed',
        'title': 'Volunteer completed a shift',
        'description': 'Starts when a volunteer clocks out of a shift.',
        'summary': '{person.name} completed {hours} h as {shift.role}',
        'fields': PERSON + [
            field('shift.role', 'string', 'Role', 'Greeter'),
            field('shift.started_at', 'datetime', 'Clocked in', '2026-12-06T08:55:00-08:00'),
            field('shift.ended_at', 'datetime', 'Clocked out', '2026-12-06T13:05:00-08:00'),
            field('hours', 'number', 'Hours', 4.17),
            field('points', 'int', 'Impact points earned', 85),
            field('event.id', 'string', 'Event ID', '17'),
            field('event.title', 'string', 'Event', 'Christmas Bazaar'),
        ],
    },
    {
        'type': 'event.published',
        'title': 'Event published',
        'description': 'Starts when an event becomes visible to volunteers.',
        'summary': '{event.title} was published',
        'fields': [
            field('event.id', 'string', 'Event ID', '17'),
            field('event.title', 'string', 'Event', 'Christmas Bazaar'),
            field('event.place', 'string', 'Place', 'Parish Hall'),
            field('event.starts_at', 'datetime', 'Event starts', '2026-12-06T09:00:00-08:00'),
            field('event.ends_at', 'datetime', 'Event ends', '2026-12-06T17:00:00-08:00'),
            field('event.url', 'string', 'Link', 'https://volunteers.example.org/en/opportunities/17/'),
        ],
    },
    {
        'type': 'training.completed',
        'title': 'Volunteer completed a training module',
        'description': 'Starts when a volunteer finishes every lesson and quiz of a training module.',
        'summary': '{person.name} completed {module.title}',
        'fields': PERSON + [
            field('module.id', 'string', 'Module ID', '5'),
            field('module.title', 'string', 'Module', 'Food Safety Basics'),
            field('completed_at', 'datetime', 'Completed', '2026-11-19T16:20:04-08:00'),
        ],
    },
]

PERSON_OUTPUT = [
    field('found', 'bool', 'Found', True),
    field('person_id', 'string', 'Person ID', '42'),
    field('name', 'string', 'Name', 'Maria K.'),
    field('email', 'string', 'Email', 'maria@example.org'),
    field('level', 'int', 'Level', 3),
    field('level_name', 'string', 'Level name', 'Pillar'),
    field('impact_points', 'int', 'Impact points', 1250),
    field('total_hours', 'number', 'Hours volunteered', 86.5),
]

ACTIONS = [
    {
        'name': 'person.find',
        'kind': 'lookup',
        'title': 'Find volunteer',
        'description': 'Looks for a volunteer by email.',
        'params': [param('email', 'string', 'Email', True, 'The address they signed up with.')],
        'output': PERSON_OUTPUT,
    },
    {
        'name': 'person.get',
        'kind': 'lookup',
        'title': 'Get volunteer',
        'description': 'Looks a volunteer up by their Town Hall person ID.',
        'params': [param('person_id', 'string', 'Person ID', True, 'The ID an event or another step gave you.')],
        'output': PERSON_OUTPUT,
    },
    {
        'name': 'person.invite',
        'kind': 'action',
        'title': 'Invite volunteer',
        'description': "Adds a volunteer with this email and emails them a welcome and a link to set their password. "
                       "If the email already has an account, nothing changes and you get that person.",
        'params': [
            param('name', 'string', 'Name', True, 'Their full name.'),
            param('email', 'string', 'Email', True),
        ],
        'output': [
            field('person_id', 'string', 'Person ID', '42'),
            field('created', 'bool', 'Account was created', True),
            field('name', 'string', 'Name', 'Maria K.'),
            field('email', 'string', 'Email', 'maria@example.org'),
        ],
    },
    {
        'name': 'notification.send',
        'kind': 'action',
        'title': 'Send notification',
        'description': 'Sends a volunteer a notification in Town Hall (and by email and to their phone, '
                       'where the organization has those switched on).',
        'params': [
            param('person_id', 'string', 'Person ID', True),
            param('title', 'string', 'Title', True),
            param('message', 'string', 'Message', True),
            param('link', 'string', 'Link', False, 'Where the notification opens: a full address, or a page of Town Hall such as /profile/.'),
        ],
        'output': [
            field('sent', 'bool', 'Sent', True),
            field('notification_id', 'string', 'Notification ID', '901'),
        ],
    },
]

for _action in ACTIONS:
    _action['method'] = 'POST'
    _action['path'] = f"/actions/{_action['name']}"

TRIGGERS_BY_TYPE = {t['type']: t for t in TRIGGERS}
ACTIONS_BY_NAME = {a['name']: a for a in ACTIONS}


def instance_name():
    from base.models import SiteSettings
    return SiteSettings.get_settings().company_name


def base_url(request):
    return f'{request.scheme}://{request.get_host()}'


def build(request):
    base = base_url(request)
    return {
        'protocol': PROTOCOL,
        'building': BUILDING,
        'name': NAME,
        'instance': instance_name(),
        # Town Hall's mark, so Switchboard draws this building's steps with it.
        'icon': base + static('base/town-hall.svg'),
        'authorize_url': base + '/switchboard/authorize',
        'token_url': base + '/switchboard/v1/token',
        'api_base': base + '/switchboard/v1',
        'triggers': TRIGGERS,
        'actions': ACTIONS,
    }
