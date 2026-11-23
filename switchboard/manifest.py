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
    field('shift.role_id', 'int', 'Role ID', 34),
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

LIST_HELP = 'at most 50'
PERSON_ID = param('person_id', 'string', 'Person ID', True, 'The ID an event or another step gave you.')
SHIFT_ID = param('shift_id', 'string', 'Shift ID', True, 'The ID of the shift at an event, from an event or a Find shift step.')
EVENT_ID = param('event_id', 'string', 'Event ID', True, 'The ID an event or a Find event step gave you.')

PERSON_FIELDS = [
    field('person_id', 'string', 'Person ID', '42'),
    field('name', 'string', 'Name', 'Maria K.'),
    field('email', 'string', 'Email', 'maria@example.org'),
    field('level', 'int', 'Level', 3),
    field('level_name', 'string', 'Level name', 'Pillar'),
    field('impact_points', 'int', 'Impact points', 1250),
    field('total_hours', 'number', 'Hours volunteered', 86.5),
    field('active', 'bool', 'Account is active', True),
    field('staff', 'bool', 'Is staff', False),
    field('phone', 'string', 'Phone', '604 555 0134'),
    field('location', 'string', 'Location', 'New Westminster'),
    field('skills', 'string', 'Skills', 'Cooking, First aid'),
    field('permanent_roles', 'string', 'Permanent roles', 'Librarian'),
    field('reliability', 'int', 'Reliability (%)', 96),
    field('joined_at', 'datetime', 'Registered', '2025-03-02T10:12:00-08:00'),
]
PERSON_OUTPUT = [field('found', 'bool', 'Found', True)] + PERSON_FIELDS
PERSON_BRIEF = [
    field('person_id', 'string', 'Person ID', '42'),
    field('name', 'string', 'Name', 'Maria K.'),
    field('email', 'string', 'Email', 'maria@example.org'),
]
SLOT_BRIEF = [
    field('shift_id', 'string', 'Shift ID', '318'),
    field('role', 'string', 'Role', 'Greeter'),
    field('starts_at', 'datetime', 'Shift starts', '2026-12-06T09:00:00-08:00'),
    field('ends_at', 'datetime', 'Shift ends', '2026-12-06T13:00:00-08:00'),
    field('event_id', 'string', 'Event ID', '17'),
    field('event_title', 'string', 'Event', 'Christmas Bazaar'),
    field('place', 'string', 'Place', 'Parish Hall'),
    field('open_spots', 'int', 'Open spots', 1),
]
SLOT_FIELDS = SLOT_BRIEF + [
    field('role_id', 'string', 'Role ID', '8'),
    field('area', 'string', 'Area', 'Kitchen'),
    field('needed', 'int', 'Volunteers needed', 4),
    field('taken', 'int', 'Volunteers signed up', 3),
    field('invite_only', 'bool', 'By invitation only', False),
    field('lead_name', 'string', 'Shift lead', 'Petro S.'),
    field('lead_email', 'string', "Shift lead's email", 'petro@example.org'),
    field('volunteers', 'string', f'Volunteers signed up ({LIST_HELP})', 'Maria K., Petro S., Ivan D.'),
    field('volunteer_emails', 'string', f"Volunteers' emails ({LIST_HELP})", 'maria@example.org, petro@example.org'),
]
EVENT_FIELDS = [
    field('event_id', 'string', 'Event ID', '17'),
    field('title', 'string', 'Event', 'Christmas Bazaar'),
    field('place', 'string', 'Place', 'Parish Hall'),
    field('address', 'string', 'Address', '501 4th Ave, New Westminster'),
    field('starts_at', 'datetime', 'Event starts', '2026-12-06T09:00:00-08:00'),
    field('ends_at', 'datetime', 'Event ends', '2026-12-06T17:00:00-08:00'),
    field('published', 'bool', 'Visible to volunteers', True),
    field('status', 'string', 'Status', 'upcoming',
          choices=choices(('upcoming', 'Upcoming'), ('live', 'Happening now'), ('past', 'Over'))),
    field('url', 'string', 'Link', 'https://volunteers.example.org/en/opportunities/17/'),
    field('volunteers_needed', 'int', 'Volunteers needed', 24),
    field('volunteers_signed_up', 'int', 'Volunteers signed up', 19),
    field('open_spots', 'int', 'Open spots', 5),
    field('coordinators', 'string', 'Coordinators', 'Sam Staff'),
    field('coordinator_emails', 'string', "Coordinators' emails", 'staff@example.org'),
    field('volunteer_emails', 'string', f"Volunteers' emails ({LIST_HELP})", 'maria@example.org, petro@example.org'),
]
FOUND = field('found', 'bool', 'Found', True)
COUNT = field('count', 'int', 'How many matched', 3)
ROLE_PARAM = param('role', 'string', 'Role', True, 'The name of the role exactly as it is in Town Hall, or its ID.')

ACTIONS = [
    # -- people: looking up --------------------------------------------------
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
        'name': 'person.search',
        'kind': 'lookup',
        'title': 'Search volunteers',
        'description': 'Finds active volunteers by name, level, permanent role or the event they signed up for. '
                       'You get how many matched, the best match (most impact points) in full, and everyone\'s names '
                       'and emails as text. Leave everything empty to count all active volunteers.',
        'params': [
            param('query', 'string', 'Name or email contains', False, max_length=100),
            param('min_level', 'int', 'At least level', False, '1 is the lowest level.', min=1, max=100),
            param('role', 'string', 'Has permanent role', False, "The role's name or ID."),
            param('event_id', 'string', 'Signed up for event', False, 'An event ID: only people signed up for a shift there.'),
        ],
        'output': [FOUND, COUNT] + PERSON_FIELDS + [
            field('names', 'string', f'Names ({LIST_HELP})', 'Maria K., Petro S., Ivan D.'),
            field('emails', 'string', f'Emails ({LIST_HELP})', 'maria@example.org, petro@example.org, ivan@example.org'),
        ],
    },
    {
        'name': 'person.next_shift',
        'kind': 'lookup',
        'title': "Get volunteer's next shift",
        'description': 'The next shift a volunteer is signed up for that has not ended, and how many they have coming up. '
                       'Found is false when they have none.',
        'params': [PERSON_ID],
        'output': [FOUND, COUNT] + SLOT_BRIEF,
    },
    {
        'name': 'shift.current',
        'kind': 'lookup',
        'title': 'Check whether a volunteer is on a shift',
        'description': 'Found is true while the volunteer is checked in and has not clocked out.',
        'params': [PERSON_ID],
        'output': [
            FOUND,
            field('role', 'string', 'Role', 'Greeter'),
            field('started_at', 'datetime', 'Clocked in', '2026-12-06T08:55:00-08:00'),
            field('shift_id', 'string', 'Shift ID', '318'),
            field('event_id', 'string', 'Event ID', '17'),
            field('event_title', 'string', 'Event', 'Christmas Bazaar'),
        ],
    },
    {
        'name': 'role.check',
        'kind': 'lookup',
        'title': 'Check whether a volunteer qualifies for a role',
        'description': 'Says whether a volunteer has finished the training and holds the certificates a role requires, '
                       'which is what signing up for it needs, and what is missing.',
        'params': [PERSON_ID, ROLE_PARAM],
        'output': [
            FOUND,
            field('qualified', 'bool', 'Qualifies', True),
            field('missing_training', 'string', 'Training still to finish', 'Food Safety Basics'),
            field('missing_certificates', 'string', 'Certificates still needed', 'First Aid'),
            field('assigned', 'bool', 'Has this permanent role', False),
            field('role_id', 'string', 'Role ID', '8'),
            field('role', 'string', 'Role', 'Greeter'),
        ],
    },
    {
        'name': 'training.status',
        'kind': 'lookup',
        'title': "Check a volunteer's training",
        'description': 'Says whether a volunteer has completed a training module, how far along they are, and whether it has expired.',
        'params': [PERSON_ID, param('module', 'string', 'Training module', True, "The module's title exactly as it is in Town Hall, or its ID.")],
        'output': [
            FOUND,
            field('completed', 'bool', 'Completed', True),
            field('progress', 'int', 'Progress (%)', 100),
            field('completed_at', 'datetime', 'Completed', '2026-11-19T16:20:04-08:00'),
            field('expired', 'bool', 'Expired', False),
            field('expires_at', 'datetime', 'Expires', '2027-11-19T16:20:04-08:00'),
            field('module_id', 'string', 'Module ID', '5'),
            field('module_title', 'string', 'Module', 'Food Safety Basics'),
        ],
    },
    {
        'name': 'certificate.status',
        'kind': 'lookup',
        'title': "Check a volunteer's certificate",
        'description': 'Says where a volunteer stands with an external certificate such as First Aid.',
        'params': [PERSON_ID, param('certificate', 'string', 'Certificate', True, "The certificate's name exactly as it is in Town Hall, or its ID.")],
        'output': [
            FOUND,
            field('status', 'string', 'Status', 'verified', choices=choices(
                ('none', 'Nothing sent in'), ('pending', 'Waiting to be checked'), ('verified', 'Verified'),
                ('rejected', 'Rejected'), ('expired', 'Expired'))),
            field('valid', 'bool', 'Holds it right now', True),
            field('expires_at', 'datetime', 'Valid until', '2027-11-19T23:59:59-08:00'),
            field('certificate_id', 'string', 'Certificate ID', '3'),
            field('certificate', 'string', 'Certificate', 'First Aid'),
        ],
    },

    # -- people: changing ----------------------------------------------------
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
        'name': 'person.update',
        'kind': 'action',
        'title': "Update volunteer's details",
        'description': "Changes a volunteer's name, phone or location. Fill in only what should change. "
                       'The email, password and access level can only be changed in Town Hall.',
        'params': [
            PERSON_ID,
            param('first_name', 'string', 'First name', False, max_length=150),
            param('last_name', 'string', 'Last name', False, max_length=150),
            param('phone', 'string', 'Phone', False, max_length=30),
            param('location', 'string', 'Location', False, 'Their city or neighbourhood.', max_length=100),
        ],
        'output': PERSON_BRIEF + [
            field('updated', 'bool', 'Something changed', True),
            field('changed', 'string', 'What changed', 'phone, location'),
            field('phone', 'string', 'Phone', '604 555 0134'),
            field('location', 'string', 'Location', 'New Westminster'),
        ],
    },
    {
        'name': 'person.deactivate',
        'kind': 'action',
        'title': 'Deactivate volunteer',
        'description': "Switches a volunteer's account off so they can no longer sign in. Nothing is deleted, and "
                       'Reactivate volunteer switches it back on. Staff accounts can only be deactivated in Town Hall.',
        'params': [PERSON_ID],
        'output': PERSON_BRIEF + [
            field('active', 'bool', 'Account is active', False),
            field('changed', 'bool', 'Something changed', True),
        ],
    },
    {
        'name': 'person.reactivate',
        'kind': 'action',
        'title': 'Reactivate volunteer',
        'description': 'Switches a deactivated account back on.',
        'params': [PERSON_ID],
        'output': PERSON_BRIEF + [
            field('active', 'bool', 'Account is active', True),
            field('changed', 'bool', 'Something changed', True),
        ],
    },
    {
        'name': 'points.award',
        'kind': 'action',
        'title': 'Award impact points',
        'description': "Adds a line to a volunteer's impact points with your reason, like an adjustment by staff. "
                       'A negative number takes points away. Their level follows their total.',
        'params': [
            PERSON_ID,
            param('points', 'int', 'Points', True, 'A whole number, not 0. Negative takes points away.', min=-10000, max=10000),
            param('reason', 'string', 'Reason', True, 'Shown to the volunteer in their points history.', max_length=200),
        ],
        'output': [
            field('entry_id', 'string', 'Points entry ID', '5012'),
            field('person_id', 'string', 'Person ID', '42'),
            field('points', 'int', 'Points', 50),
            field('impact_points', 'int', 'Impact points in total', 1300),
            field('level', 'int', 'Level', 3),
            field('level_name', 'string', 'Level name', 'Pillar'),
        ],
    },
    {
        'name': 'endorsement.give',
        'kind': 'action',
        'title': 'Give an endorsement',
        'description': 'Endorses a volunteer for a skill in the name of another person, who must have an account '
                       '(for example a manager whose praise was recorded in another app). The volunteer is told, and '
                       "Town Hall's usual rules decide whether it earns them points. Nothing happens if that person "
                       'already endorsed them for the skill.',
        'params': [
            PERSON_ID,
            param('from_person_id', 'string', 'Endorsed by (person ID)', True, 'The person giving the endorsement.'),
            param('skill', 'string', 'Skill', True, "The skill's name exactly as it is in Town Hall, or its ID."),
            param('message', 'string', 'Message', False, max_length=1000),
            param('event_id', 'string', 'Event ID', False, 'The event they worked at together, if any.'),
        ],
        'output': [
            field('endorsement_id', 'string', 'Endorsement ID', '77'),
            field('created', 'bool', 'Endorsement was given', True),
            field('points', 'int', 'Impact points it earned', 5),
            field('skill', 'string', 'Skill', 'Teamwork'),
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

    # -- roles ---------------------------------------------------------------
    {
        'name': 'role.find',
        'kind': 'lookup',
        'title': 'Find role',
        'description': 'Looks for a role by name. You get how many matched, the first in full and all their names.',
        'params': [param('name', 'string', 'Name contains', False, 'Leave empty for every role.', max_length=50)],
        'output': [
            FOUND, COUNT,
            field('role_id', 'string', 'Role ID', '8'),
            field('name', 'string', 'Role', 'Greeter'),
            field('permanent', 'bool', 'Permanent role', False),
            field('description', 'string', 'Description', 'Welcomes guests at the door.'),
            field('names', 'string', f'Names ({LIST_HELP})', 'Greeter, Kitchen helper'),
        ],
    },
    {
        'name': 'role.assign',
        'kind': 'action',
        'title': 'Assign a permanent role',
        'description': 'Gives a volunteer a permanent role (a standing position such as Librarian), so they can start '
                       'shifts in it at the kiosk or from another app.',
        'params': [PERSON_ID, ROLE_PARAM],
        'output': [
            field('person_id', 'string', 'Person ID', '42'),
            field('role_id', 'string', 'Role ID', '8'),
            field('role', 'string', 'Role', 'Librarian'),
            field('assigned', 'bool', 'Has the role now', True),
            field('changed', 'bool', 'Something changed', True),
            field('permanent_roles', 'string', 'Their permanent roles', 'Librarian, Café helper'),
        ],
    },
    {
        'name': 'role.unassign',
        'kind': 'action',
        'title': 'Take a permanent role away',
        'description': 'Takes a permanent role away from a volunteer. Their past shifts and points stay.',
        'params': [PERSON_ID, ROLE_PARAM],
        'output': [
            field('person_id', 'string', 'Person ID', '42'),
            field('role_id', 'string', 'Role ID', '8'),
            field('role', 'string', 'Role', 'Librarian'),
            field('assigned', 'bool', 'Has the role now', False),
            field('changed', 'bool', 'Something changed', True),
            field('permanent_roles', 'string', 'Their permanent roles', 'Café helper'),
        ],
    },

    # -- events --------------------------------------------------------------
    {
        'name': 'event.get',
        'kind': 'lookup',
        'title': 'Get event',
        'description': 'Looks an event up by its ID, with how its staffing stands.',
        'params': [EVENT_ID],
        'output': [FOUND] + EVENT_FIELDS,
    },
    {
        'name': 'event.find',
        'kind': 'lookup',
        'title': 'Find events',
        'description': 'Looks for events by title. You get how many matched, the soonest upcoming one (or the latest '
                       'past one) in full, and all their titles.',
        'params': [
            param('query', 'string', 'Title contains', False, 'Leave empty for every event.', max_length=100),
            param('when', 'string', 'When', False, default='upcoming',
                  choices=choices(('upcoming', 'Upcoming or happening now'), ('past', 'Over'))),
            param('include_hidden', 'bool', 'Include events hidden from volunteers', False, default=False),
        ],
        'output': [FOUND, COUNT] + EVENT_FIELDS + [
            field('titles', 'string', f'Titles ({LIST_HELP})', 'Christmas Bazaar, New Year Dinner'),
        ],
    },
    {
        'name': 'event.create',
        'kind': 'action',
        'title': 'Create event',
        'description': 'Creates an event that is hidden from volunteers until someone publishes it. Add its shifts '
                       'with Add shift to event. Pictures, categories and areas are added in Town Hall.',
        'params': [
            param('title', 'string', 'Title', True, max_length=100),
            param('starts_at', 'datetime', 'Starts', True),
            param('ends_at', 'datetime', 'Ends', True),
            param('description', 'string', 'Description', False, max_length=5000),
            param('venue', 'string', 'Venue', False, 'The name of a saved venue exactly as it is in Town Hall, or its ID.'),
            param('place', 'string', 'Address', False, 'A one-off address, used when there is no venue.', max_length=200),
            param('attendees', 'int', 'Expected attendees', False, min=0, max=1000000),
            param('coordinator_id', 'string', 'Coordinator (person ID)', False,
                  'Who runs the event. Left empty, it is the staff member who connected Switchboard.'),
        ],
        'output': EVENT_FIELDS + [field('created', 'bool', 'Event was created', True)],
    },
    {
        'name': 'event.update',
        'kind': 'action',
        'title': 'Update event',
        'description': "Changes an event's title, description, times or expected attendees. Fill in only what should "
                       'change. People who signed up are not told automatically.',
        'params': [
            EVENT_ID,
            param('title', 'string', 'Title', False, max_length=100),
            param('description', 'string', 'Description', False, max_length=5000),
            param('starts_at', 'datetime', 'Starts', False),
            param('ends_at', 'datetime', 'Ends', False),
            param('attendees', 'int', 'Attendees', False, 'Used when working out impact points.', min=0, max=1000000),
        ],
        'output': EVENT_FIELDS + [
            field('updated', 'bool', 'Something changed', True),
            field('changed', 'string', 'What changed', 'starts_at, ends_at'),
        ],
    },
    {
        'name': 'event.publish',
        'kind': 'action',
        'title': 'Publish event',
        'description': 'Makes an event visible to volunteers so they can sign up. Unlike the Publish button in Town '
                       'Hall, this does not email or push an announcement; send your own in the next step.',
        'params': [EVENT_ID],
        'output': [
            field('event_id', 'string', 'Event ID', '17'),
            field('title', 'string', 'Event', 'Christmas Bazaar'),
            field('published', 'bool', 'Visible to volunteers', True),
            field('changed', 'bool', 'Something changed', True),
            field('url', 'string', 'Link', 'https://volunteers.example.org/en/opportunities/17/'),
        ],
    },
    {
        'name': 'event.unpublish',
        'kind': 'action',
        'title': 'Hide event',
        'description': 'Hides an event from volunteers. Sign-ups stay as they are, and nothing is deleted.',
        'params': [EVENT_ID],
        'output': [
            field('event_id', 'string', 'Event ID', '17'),
            field('title', 'string', 'Event', 'Christmas Bazaar'),
            field('published', 'bool', 'Visible to volunteers', False),
            field('changed', 'bool', 'Something changed', True),
            field('url', 'string', 'Link', 'https://volunteers.example.org/en/opportunities/17/'),
        ],
    },
    {
        'name': 'event.add_shift',
        'kind': 'action',
        'title': 'Add shift to event',
        'description': 'Adds a shift for a role to an event: when it runs and how many volunteers it needs.',
        'params': [
            EVENT_ID,
            param('role', 'string', 'Role', True, 'The name of an event role exactly as it is in Town Hall, or its ID.'),
            param('starts_at', 'datetime', 'Starts', True),
            param('ends_at', 'datetime', 'Ends', True),
            param('needed', 'int', 'Volunteers needed', False, min=1, max=500, default=1),
            param('extra', 'int', 'Extra volunteers allowed', False, min=0, max=500, default=0),
            param('invite_only', 'bool', 'By invitation only', False, 'Hidden from the public list; people join by invitation.', default=False),
        ],
        'output': SLOT_FIELDS + [field('created', 'bool', 'Shift was added', True)],
    },
    {
        'name': 'event.notify_volunteers',
        'kind': 'action',
        'title': 'Message everyone signed up for an event',
        'description': 'Sends your message to every volunteer signed up for a shift at the event: a notification in '
                       'Town Hall, and by email and to their phone where the organization has those switched on.',
        'params': [
            EVENT_ID,
            param('message', 'string', 'Message', True, max_length=2000),
        ],
        'output': [
            field('sent', 'int', 'People messaged', 19),
            field('event_id', 'string', 'Event ID', '17'),
            field('title', 'string', 'Event', 'Christmas Bazaar'),
        ],
    },

    # -- shifts at events ----------------------------------------------------
    {
        'name': 'shift.get',
        'kind': 'lookup',
        'title': 'Get shift',
        'description': 'Looks a shift of an event up by its ID: its role, times, who is on it and how many spots are open.',
        'params': [SHIFT_ID],
        'output': [FOUND] + SLOT_FIELDS,
    },
    {
        'name': 'shift.find',
        'kind': 'lookup',
        'title': 'Find shift at an event',
        'description': "Looks through an event's shifts, earliest first. You get how many matched and the first in "
                       'full, so you can sign someone up for it or invite them.',
        'params': [
            EVENT_ID,
            param('role', 'string', 'Role', False, "Only shifts of this role: its name or ID."),
            param('open_only', 'bool', 'Only shifts with open spots', False, default=False),
        ],
        'output': [FOUND, COUNT] + SLOT_FIELDS,
    },
    {
        'name': 'shift.sign_up',
        'kind': 'action',
        'title': 'Sign volunteer up for a shift',
        'description': 'Signs a volunteer up for a shift at an event, under the same rules as when they do it themselves: '
                       'the required training and certificates, an open spot, no clash with their other shifts, and an '
                       'invitation for invite-only shifts. They get the usual confirmation.',
        'params': [PERSON_ID, SHIFT_ID],
        'output': SLOT_BRIEF + [
            field('signed_up', 'bool', 'Is signed up', True),
            field('created', 'bool', 'Was signed up just now', True),
        ],
    },
    {
        'name': 'shift.cancel',
        'kind': 'action',
        'title': 'Take volunteer off a shift',
        'description': 'Cancels a volunteer\'s sign-up, as if they cancelled it themselves: less than 24 hours before '
                       'the shift it counts against their reliability and the coordinators are told. A shift they '
                       'already checked in for, or that is over, can\'t be cancelled.',
        'params': [PERSON_ID, SHIFT_ID],
        'output': SLOT_BRIEF + [
            field('cancelled', 'bool', 'Was taken off', True),
            field('late', 'bool', 'Less than 24 hours before the shift', False),
        ],
    },
    {
        'name': 'shift.invite',
        'kind': 'action',
        'title': 'Invite volunteer to a shift',
        'description': 'Sends a volunteer an invitation to a shift (a notification and an email). They decide whether '
                       'to accept; nobody is signed up without agreeing.',
        'params': [
            PERSON_ID, SHIFT_ID,
            param('message', 'string', 'Personal note', False, '[Name] is replaced with their first name.', max_length=2000),
        ],
        'output': SLOT_BRIEF + [
            field('invited', 'bool', 'Invitation was sent', True),
            field('invite_id', 'string', 'Invitation ID', '230'),
        ],
    },
    {
        'name': 'shift.start',
        'kind': 'action',
        'title': 'Check volunteer in',
        'description': 'Starts a volunteer\'s shift now. Give the shift ID of a shift at an event they are signed up '
                       'for (from an hour before it starts until it ends), or a permanent role they are assigned to. '
                       'End it with Check volunteer out: a shift started here never ends by itself.',
        'params': [
            PERSON_ID,
            param('shift_id', 'string', 'Shift ID', False, 'A shift at an event. Leave empty for a permanent role.'),
            param('role', 'string', 'Permanent role', False, "The permanent role's name or ID. Leave empty for a shift at an event."),
        ],
        'output': [
            field('started', 'bool', 'Was checked in just now', True),
            field('role', 'string', 'Role', 'Librarian'),
            field('started_at', 'datetime', 'Clocked in', '2026-12-06T08:55:00-08:00'),
            field('shift_id', 'string', 'Shift ID', '318'),
            field('event_id', 'string', 'Event ID', '17'),
            field('event_title', 'string', 'Event', 'Christmas Bazaar'),
        ],
    },
    {
        'name': 'shift.end',
        'kind': 'action',
        'title': 'Check volunteer out',
        'description': 'Ends the shift a volunteer is on now and records their hours and impact points. '
                       'Nothing happens if they are not on a shift.',
        'params': [PERSON_ID],
        'output': [
            field('ended', 'bool', 'Was checked out just now', True),
            field('role', 'string', 'Role', 'Librarian'),
            field('started_at', 'datetime', 'Clocked in', '2026-12-06T08:55:00-08:00'),
            field('ended_at', 'datetime', 'Clocked out', '2026-12-06T13:05:00-08:00'),
            field('hours', 'number', 'Hours', 4.17),
            field('points', 'int', 'Impact points earned', 85),
            field('event_id', 'string', 'Event ID', '17'),
            field('event_title', 'string', 'Event', 'Christmas Bazaar'),
        ],
    },

    # -- training and venues -------------------------------------------------
    {
        'name': 'training.find',
        'kind': 'lookup',
        'title': 'Find training module',
        'description': 'Looks for a training module by title. You get how many matched, the first in full and all their titles.',
        'params': [param('title', 'string', 'Title contains', False, 'Leave empty for every module.', max_length=100)],
        'output': [
            FOUND, COUNT,
            field('module_id', 'string', 'Module ID', '5'),
            field('title', 'string', 'Module', 'Food Safety Basics'),
            field('published', 'bool', 'Available to volunteers', True),
            field('minutes', 'int', 'Length in minutes', 35),
            field('url', 'string', 'Link', 'https://volunteers.example.org/en/training/module/5/'),
            field('titles', 'string', f'Titles ({LIST_HELP})', 'Food Safety Basics, Welcome Desk'),
        ],
    },
    {
        'name': 'venue.find',
        'kind': 'lookup',
        'title': 'Find venue',
        'description': 'Looks for a saved venue by name. You get how many matched, the first in full and all their names.',
        'params': [param('name', 'string', 'Name contains', False, 'Leave empty for every venue.', max_length=100)],
        'output': [
            FOUND, COUNT,
            field('venue_id', 'string', 'Venue ID', '2'),
            field('name', 'string', 'Venue', 'Parish Hall'),
            field('address', 'string', 'Address', '501 4th Ave, New Westminster'),
            field('phone', 'string', 'Phone', '604 555 0100'),
            field('url', 'string', 'Link', 'https://volunteers.example.org/en/venues/2/'),
            field('names', 'string', f'Names ({LIST_HELP})', 'Parish Hall, Community Kitchen'),
        ],
    },
    {
        'name': 'venue.post_note',
        'kind': 'action',
        'title': 'Post a notice at a venue',
        'description': "Posts a short notice on a venue's page and kiosk (for example \"Side entrance closed\"). "
                       'It disappears by itself after the days you choose.',
        'params': [
            param('venue', 'string', 'Venue', True, "The venue's name exactly as it is in Town Hall, or its ID."),
            param('note', 'string', 'Notice', True, max_length=500),
            param('days', 'int', 'Show for', False, default=7, choices=choices(
                (1, '1 day'), (3, '3 days'), (7, '1 week'), (14, '2 weeks'), (30, '30 days'), (365, '1 year'))),
        ],
        'output': [
            field('note_id', 'string', 'Notice ID', '64'),
            field('venue_id', 'string', 'Venue ID', '2'),
            field('venue', 'string', 'Venue', 'Parish Hall'),
            field('expires_at', 'datetime', 'Shown until', '2026-11-26T16:20:04-08:00'),
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
