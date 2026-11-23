"""What Town Hall offers Switchboard: the triggers it emits and the actions it runs.

Switchboard builds its nodes from this, and workflows store the `type` and `name` values and the
field keys, so they must never be renamed or change type: add, never rename. Titles and
descriptions are shown to people in Switchboard, in English (the protocol's language), so they
aren't translated here.

Shapes shared by many entries, so workflows can mix them: a person is `person.id|name|email`,
a role slot of an event is `shift.*` with its `event.id|title|place` (`shift.id` is always the
slot's ID, the one the shift actions take), an event is `event.*`.
"""
from django.templatetags.static import static

PROTOCOL = '1'
BUILDING = 'town-hall'
NAME = 'Town Hall'


def field(key, type_, label, example, **extra):
    return {'key': key, 'type': type_, 'label': label, 'example': example, **extra}


def param(key, type_, label, required=False, help='', **limits):
    """A setting of an action. `limits`: max_length (text), min / max (numbers), choices
    ([{'value', 'label'}]) and default; the API refuses values outside them (views._check_params)."""
    spec = {'key': key, 'type': type_, 'label': label, 'required': required}
    if help:
        spec['help'] = help
    spec.update(limits)
    return spec


def choices(*pairs):
    return [{'value': value, 'label': label} for value, label in pairs]


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------

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
    field('shift.area', 'string', 'Area', 'Kitchen'),
    field('shift.needed', 'int', 'Volunteers needed', 4),
    field('shift.taken', 'int', 'Volunteers signed up', 3),
    field('shift.open_spots', 'int', 'Open spots', 1),
]
EVENT = [
    field('event.id', 'string', 'Event ID', '17'),
    field('event.title', 'string', 'Event', 'Christmas Bazaar'),
    field('event.place', 'string', 'Place', 'Parish Hall'),
    field('event.starts_at', 'datetime', 'Event starts', '2026-12-06T09:00:00-08:00'),
    field('event.ends_at', 'datetime', 'Event ends', '2026-12-06T17:00:00-08:00'),
    field('event.url', 'string', 'Link', 'https://volunteers.example.org/en/opportunities/17/'),
]
EVENT_SHORT = [
    field('event.id', 'string', 'Event ID', '17'),
    field('event.title', 'string', 'Event', 'Christmas Bazaar'),
]
STAFFING = [
    field('volunteers_needed', 'int', 'Volunteers needed', 24),
    field('volunteers_signed_up', 'int', 'Volunteers signed up', 19),
    field('open_spots', 'int', 'Open spots', 5),
]
MODULE = [
    field('module.id', 'string', 'Module ID', '5'),
    field('module.title', 'string', 'Module', 'Food Safety Basics'),
]
CERTIFICATE = [
    field('certificate.id', 'string', 'Certificate ID', '3'),
    field('certificate.name', 'string', 'Certificate', 'First Aid'),
]
ROLE = [
    field('role.id', 'string', 'Role ID', '8'),
    field('role.name', 'string', 'Role', 'Librarian'),
]
HOURS_UNTIL = field('hours_until', 'number', 'Hours until it starts', 23.5)
POINT_SOURCES = choices(('shift', 'Shift'), ('training', 'Training'), ('endorsement', 'Endorsement'),
                        ('profile', 'Profile completed'), ('adjustment', 'Adjustment'))

TRIGGERS = [
    # -- people ------------------------------------------------------------
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
        'type': 'volunteer.profile_completed',
        'title': 'Volunteer completed their profile',
        'description': 'Starts the first time a volunteer has a photo, a bio, skills and a location on their profile '
                       '(when they get the one-time points for it).',
        'summary': '{person.name} completed their profile',
        'fields': PERSON + [field('points', 'int', 'Impact points earned', 25)],
    },
    {
        'type': 'volunteer.email_changed',
        'title': "Volunteer's email changed",
        'description': 'Starts when the email on an account changes. People are matched between your apps by email, '
                       'so use it to keep the other apps in step.',
        'summary': '{person.name} now uses {person.email}',
        'fields': PERSON + [field('previous_email', 'string', 'Previous email', 'maria.old@example.org')],
    },
    {
        'type': 'volunteer.deactivated',
        'title': 'Volunteer deactivated',
        'description': "Starts when staff deactivate someone's account, so they can no longer sign in.",
        'summary': '{person.name} was deactivated',
        'fields': PERSON,
    },
    {
        'type': 'volunteer.reactivated',
        'title': 'Volunteer reactivated',
        'description': 'Starts when staff switch a deactivated account back on.',
        'summary': '{person.name} was reactivated',
        'fields': PERSON,
    },
    {
        'type': 'volunteer.role_assigned',
        'title': 'Volunteer given a permanent role',
        'description': 'Starts when staff assign someone a permanent role (a standing position such as Librarian), '
                       'which lets them start shifts in it.',
        'summary': '{person.name} is now {role.name}',
        'fields': PERSON + ROLE,
    },
    {
        'type': 'volunteer.role_unassigned',
        'title': 'Permanent role taken away',
        'description': 'Starts when staff take a permanent role away from someone.',
        'summary': '{person.name} is no longer {role.name}',
        'fields': PERSON + ROLE,
    },
    {
        'type': 'points.awarded',
        'title': 'Impact points recorded',
        'description': "Starts for every new line in a volunteer's impact points: a completed shift, a finished "
                       'training module, an endorsement, a completed profile, or an adjustment by staff (which can be negative).',
        'summary': '{person.name}: {points} points ({reason})',
        'fields': PERSON + [
            field('points', 'int', 'Points', 85),
            field('source', 'string', 'What for', 'shift', choices=POINT_SOURCES),
            field('reason', 'string', 'Reason', 'Greeter at Christmas Bazaar'),
            field('impact_points', 'int', 'Impact points in total', 1250),
        ],
    },
    {
        'type': 'endorsement.given',
        'title': 'Volunteer was endorsed',
        'description': 'Starts when someone endorses a volunteer for one or more skills. The person is the one who was endorsed.',
        'summary': '{endorser.name} endorsed {person.name} for {skills}',
        'fields': PERSON + [
            field('endorser.id', 'string', 'Endorsed by: person ID', '57'),
            field('endorser.name', 'string', 'Endorsed by', 'Petro S.'),
            field('endorser.email', 'string', 'Endorsed by: email', 'petro@example.org'),
            field('skills', 'string', 'Skills', 'Teamwork, Cooking'),
            field('message', 'string', 'Message', 'Kept the kitchen calm all day.'),
        ] + EVENT_SHORT,
    },

    # -- sign-ups and invitations --------------------------------------------
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
        'fields': PERSON + SLOT + [
            field('late', 'bool', 'Less than 24 hours before the shift', False),
        ],
    },
    {
        'type': 'shift.filled',
        'title': 'Shift is fully staffed',
        'description': 'Starts when a shift gets the last volunteer it needs.',
        'summary': '{shift.role} at {event.title} is fully staffed',
        'fields': SLOT,
    },
    {
        'type': 'shift.understaffed',
        'title': 'Shift is still short of volunteers',
        'description': 'Starts once for a shift of a published event that still needs volunteers 48 hours before it '
                       'begins (or when Town Hall first notices, if that is later).',
        'summary': '{shift.role} at {event.title} still needs {shift.open_spots}',
        'fields': SLOT + [HOURS_UNTIL],
    },
    {
        'type': 'shift.starting_soon',
        'title': "Volunteer's shift starts within 24 hours",
        'description': "Starts once for each volunteer on a shift of a published event when it is 24 hours away (right "
                       'after they sign up, if they sign up later than that).',
        'summary': "{person.name}'s {shift.role} shift at {event.title} starts soon",
        'fields': PERSON + SLOT + [HOURS_UNTIL],
    },
    {
        'type': 'shift.lead_assigned',
        'title': 'Shift lead picked',
        'description': 'Starts when a volunteer becomes the shift lead of their shift, picked by a coordinator or '
                       'automatically 48 hours before the event.',
        'summary': '{person.name} leads {shift.role} at {event.title}',
        'fields': PERSON + SLOT + [field('automatic', 'bool', 'Picked automatically', False)],
    },
    {
        'type': 'area.lead_assigned',
        'title': 'Area lead assigned',
        'description': "Starts when a coordinator makes someone the lead of an area of an event with a chain of command. "
                       'The shift is their area lead shift.',
        'summary': '{person.name} leads {area.name} at {event.title}',
        'fields': PERSON + SLOT + [
            field('area.id', 'string', 'Area ID', '4'),
            field('area.name', 'string', 'Area', 'Kitchen'),
        ],
    },
    {
        'type': 'invite.sent',
        'title': 'Volunteer invited to a shift',
        'description': 'Starts when staff invite a volunteer to a shift.',
        'summary': '{person.name} was invited as {shift.role} at {event.title}',
        'fields': PERSON + SLOT,
    },
    {
        'type': 'invite.accepted',
        'title': 'Invitation accepted',
        'description': 'Starts when a volunteer accepts an invitation and is signed up for the shift.',
        'summary': '{person.name} accepted {shift.role} at {event.title}',
        'fields': PERSON + SLOT,
    },
    {
        'type': 'invite.declined',
        'title': 'Invitation declined',
        'description': 'Starts when a volunteer declines an invitation to a shift.',
        'summary': '{person.name} declined {shift.role} at {event.title}',
        'fields': PERSON + SLOT,
    },

    # -- working a shift -----------------------------------------------------
    {
        'type': 'shift.started',
        'title': 'Volunteer checked in',
        'description': 'Starts when a volunteer starts working: at a kiosk, checked in by staff, or through another app. '
                       'For a permanent role there is no event and no shift ID.',
        'summary': '{person.name} checked in as {shift.role}',
        'fields': PERSON + [
            field('shift.id', 'string', 'Shift ID', '318'),
            field('shift.role', 'string', 'Role', 'Greeter'),
            field('shift.started_at', 'datetime', 'Clocked in', '2026-12-06T08:55:00-08:00'),
            field('walk_in', 'bool', 'Covered an open shift without signing up', False),
            field('event.id', 'string', 'Event ID', '17'),
            field('event.title', 'string', 'Event', 'Christmas Bazaar'),
            field('event.place', 'string', 'Place', 'Parish Hall'),
        ],
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
        'type': 'shift.no_show',
        'title': 'Volunteer missed a shift',
        'description': 'Starts after a shift ends for each volunteer who was signed up but never checked in, or checked '
                       'in after more than half of it had passed. It is what counts against their reliability.',
        'summary': '{person.name} missed {shift.role} at {event.title}',
        'fields': PERSON + SLOT + [field('checked_in_late', 'bool', 'Checked in, but too late', False)],
    },

    # -- events --------------------------------------------------------------
    {
        'type': 'event.created',
        'title': 'Event created',
        'description': 'Starts when staff create an event. It may still be hidden from volunteers.',
        'summary': '{event.title} was created',
        'fields': EVENT + [field('event.published', 'bool', 'Visible to volunteers', False)],
    },
    {
        'type': 'event.published',
        'title': 'Event published',
        'description': 'Starts when an event becomes visible to volunteers.',
        'summary': '{event.title} was published',
        'fields': EVENT,
    },
    {
        'type': 'event.unpublished',
        'title': 'Event hidden',
        'description': 'Starts when staff hide a published event from volunteers again, for example because it is called off.',
        'summary': '{event.title} was hidden from volunteers',
        'fields': EVENT,
    },
    {
        'type': 'event.rescheduled',
        'title': 'Event moved to another time',
        'description': "Starts when an event's start or end time changes.",
        'summary': '{event.title} now starts {event.starts_at}',
        'fields': EVENT + [
            field('event.published', 'bool', 'Visible to volunteers', True),
            field('previous_starts_at', 'datetime', 'Used to start', '2026-12-05T09:00:00-08:00'),
            field('previous_ends_at', 'datetime', 'Used to end', '2026-12-05T17:00:00-08:00'),
        ],
    },
    {
        'type': 'event.starting_soon',
        'title': 'Event starts within 24 hours',
        'description': 'Starts once for a published event when it is 24 hours away, with how its staffing stands.',
        'summary': '{event.title} starts soon ({open_spots} spots open)',
        'fields': EVENT + STAFFING + [HOURS_UNTIL],
    },
    {
        'type': 'event.started',
        'title': 'Event started',
        'description': "Starts once when a published event's start time arrives.",
        'summary': '{event.title} started',
        'fields': EVENT + STAFFING,
    },
    {
        'type': 'event.ended',
        'title': 'Event ended',
        'description': "Starts once when a published event's end time has passed, with what was logged so far.",
        'summary': '{event.title} ended: {volunteers} volunteers, {hours} h',
        'fields': EVENT + [
            field('volunteers', 'int', 'Volunteers who checked in', 21),
            field('hours', 'number', 'Hours logged', 88.5),
            field('volunteers_signed_up', 'int', 'Volunteers signed up', 24),
        ],
    },
    {
        'type': 'event.coordinator_added',
        'title': 'Coordinator added to an event',
        'description': 'Starts when someone is made a general coordinator of an event.',
        'summary': '{person.name} coordinates {event.title}',
        'fields': PERSON + EVENT,
    },
    {
        'type': 'event.feedback_submitted',
        'title': 'Volunteer left feedback on an event',
        'description': 'Starts when a volunteer rates an event they took part in for the first time.',
        'summary': '{person.name} rated {event.title} {rating}/5',
        'fields': PERSON + EVENT_SHORT + [
            field('rating', 'int', 'Rating (1 to 5)', 5),
            field('enjoyed', 'string', 'What they enjoyed', 'Great team and clear instructions.'),
            field('suggestions', 'string', 'Suggestions', 'More signs at the entrance.'),
        ],
    },

    # -- training and certificates -------------------------------------------
    {
        'type': 'training.published',
        'title': 'Training module published',
        'description': 'Starts when a training module becomes available to volunteers.',
        'summary': '{module.title} was published',
        'fields': MODULE + [field('module.url', 'string', 'Link', 'https://volunteers.example.org/en/training/module/5/')],
    },
    {
        'type': 'training.started',
        'title': 'Volunteer started a training module',
        'description': 'Starts when a volunteer finishes their first lesson of a training module.',
        'summary': '{person.name} started {module.title}',
        'fields': PERSON + MODULE,
    },
    {
        'type': 'training.quiz_passed',
        'title': 'Volunteer passed a quiz',
        'description': 'Starts the first time a volunteer passes a quiz of a training module.',
        'summary': '{person.name} passed {quiz.title}',
        'fields': PERSON + MODULE + [
            field('quiz.id', 'string', 'Quiz ID', '12'),
            field('quiz.title', 'string', 'Quiz', 'Food Safety Quiz'),
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
    {
        'type': 'training.expired',
        'title': "Volunteer's training expired",
        'description': 'Starts once when a completed training module that has to be renewed reaches its expiry date.',
        'summary': "{person.name}'s {module.title} expired",
        'fields': PERSON + MODULE + [
            field('completed_at', 'datetime', 'Completed', '2025-11-19T16:20:04-08:00'),
            field('expired_at', 'datetime', 'Expired', '2026-11-19T16:20:04-08:00'),
        ],
    },
    {
        'type': 'certificate.submitted',
        'title': 'Certificate sent in for checking',
        'description': 'Starts when a volunteer uploads documents for an external certificate (such as First Aid), '
                       'or uploads them again after a rejection.',
        'summary': '{person.name} sent in {certificate.name}',
        'fields': PERSON + CERTIFICATE + [field('resubmitted', 'bool', 'Sent again after a rejection', False)],
    },
    {
        'type': 'certificate.approved',
        'title': 'Certificate approved',
        'description': "Starts when staff verify a volunteer's external certificate.",
        'summary': "{person.name}'s {certificate.name} was approved",
        'fields': PERSON + CERTIFICATE + [field('expires_at', 'datetime', 'Valid until', '2027-11-19T23:59:59-08:00')],
    },
    {
        'type': 'certificate.rejected',
        'title': 'Certificate rejected',
        'description': "Starts when staff reject the documents a volunteer sent in for an external certificate.",
        'summary': "{person.name}'s {certificate.name} was rejected",
        'fields': PERSON + CERTIFICATE + [field('notes', 'string', "Reviewer's notes", 'The scan is unreadable.')],
    },
    {
        'type': 'certificate.expiring',
        'title': 'Certificate expires within 30 days',
        'description': "Starts once when a volunteer's verified certificate is 30 days from its expiry date.",
        'summary': "{person.name}'s {certificate.name} expires in {days_left} days",
        'fields': PERSON + CERTIFICATE + [
            field('expires_at', 'datetime', 'Valid until', '2026-12-19T23:59:59-08:00'),
            field('days_left', 'int', 'Days left', 30),
        ],
    },
    {
        'type': 'certificate.expired',
        'title': 'Certificate expired',
        'description': "Starts once when a volunteer's verified certificate passes its expiry date.",
        'summary': "{person.name}'s {certificate.name} expired",
        'fields': PERSON + CERTIFICATE + [field('expires_at', 'datetime', 'Was valid until', '2026-11-19T23:59:59-08:00')],
    },

    # -- venues --------------------------------------------------------------
    {
        'type': 'venue.note_posted',
        'title': 'Notice posted at a venue',
        'description': "Starts when staff post a notice on a venue (shown on the venue's page and kiosk).",
        'summary': '{venue.name}: {note}',
        'fields': [
            field('venue.id', 'string', 'Venue ID', '2'),
            field('venue.name', 'string', 'Venue', 'Parish Hall'),
            field('note', 'string', 'Notice', 'Side entrance closed for repairs this week.'),
            field('expires_at', 'datetime', 'Shown until', '2026-11-26T16:20:04-08:00'),
        ],
    },
]


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

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
