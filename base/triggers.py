"""App events that can send an email (console > Communication > Email).

Each trigger has its own listmonk template per enabled language, seeded from
``base/templates/base/listmonk/<key>.html`` (see base/email_templates.py) and
stored as ``EmailTemplate`` rows. On/off and campaign options are ``EmailTrigger``
rows. ``fields`` lists the data a template can use as {{ .Tx.Data.<field> }}, on top
of ``COMMON_FIELDS``.

``campaign`` triggers go to the "Town Hall users" list (plus any lists staff add) as a
listmonk campaign built from a visual template, so unsubscribes are respected.
"""
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

COMMON_FIELDS = ('name', 'org')


@dataclass(frozen=True)
class Trigger:
    key: str
    group: str
    icon: str
    label: str
    description: str
    audience: str
    subject: str = ''      # seeded subject; %(field)s placeholders become {{ .Tx.Data.field }}
    fields: tuple = ()
    default_on: bool = False
    required: bool = False   # can't be switched off
    campaign: bool = False


ACCOUNT, EVENTS, PEOPLE, TRAINING, SYSTEM = _('Accounts'), _('Events'), _('People'), _('Training'), _('System')

TRIGGERS = [
    Trigger('welcome', ACCOUNT, 'waving_hand', _('Account created'),
            _('Someone signs up, or staff add a volunteer from the console.'), _('The new volunteer'),
            _('Welcome to %(org)s'), ('link', 'id_code')),
    Trigger('password_reset', ACCOUNT, 'lock_reset', _('Password reset requested'),
            _('Someone asks for a password reset link.'), _('The account holder'),
            _('Reset your %(org)s password'), ('link',), default_on=True, required=True),
    Trigger('event_published', EVENTS, 'campaign', _('New event published'),
            _('An event becomes visible to volunteers.'), _('Everyone on "Town Hall users", plus any lists you add'),
            _('New opportunity: %(event)s'), campaign=True),
    Trigger('invitation', EVENTS, 'mail', _('Invitation received'),
            _('Staff invite a volunteer to a role from the console.'), _('The invited volunteer'),
            '%(subject)s', ('subject', 'event', 'role', 'date', 'link', 'message', 'message_html'), default_on=True),
    Trigger('signup_confirmed', EVENTS, 'event_available', _('Sign-up confirmed'),
            _('A volunteer signs up for a role or accepts an invitation.'), _('The volunteer'),
            _("You're signed up: %(event)s"), ('event', 'role', 'date', 'location', 'link')),
    Trigger('event_message', EVENTS, 'forum', _('Message to event volunteers'),
            _('Staff use "Message All Volunteers" on an event.'), _("The event's volunteers"),
            _('News about %(event)s'), ('event', 'message', 'message_html', 'link')),
    Trigger('volunteer_message', PEOPLE, 'chat', _('Message to a volunteer'),
            _("Staff send a message from a volunteer's profile."), _('That volunteer'),
            _('A message from %(org)s'), ('message', 'message_html', 'link')),
    Trigger('endorsement_received', PEOPLE, 'thumb_up', _('Endorsement received'),
            _('Someone endorses a volunteer for a skill.'), _('The endorsed volunteer'),
            _('%(from_name)s endorsed you'), ('from_name', 'skills', 'message', 'link')),
    Trigger('training_completed', TRAINING, 'school', _('Training module completed'),
            _('A volunteer finishes a training module.'), _('The volunteer'),
            _('You completed %(module)s'), ('module', 'link')),
    Trigger('certificate_approved', TRAINING, 'verified', _('Certificate approved'),
            _('Staff verify an uploaded certificate.'), _('The volunteer'),
            _('Your %(certificate)s certificate was verified'), ('certificate', 'notes', 'link')),
    Trigger('certificate_rejected', TRAINING, 'assignment_return', _('Certificate rejected'),
            _('Staff send a certificate back for another upload.'), _('The volunteer'),
            _('Your %(certificate)s documents need another look'), ('certificate', 'notes', 'link')),
    Trigger('system', SYSTEM, 'settings', _('Test and system emails'),
            _('"Send test email" and any other message without an event of its own.'), _('Varies'),
            '%(subject)s', ('subject', 'body', 'html'), default_on=True, required=True),
]

BY_KEY = {t.key: t for t in TRIGGERS}

# What each {{ .Tx.Data.x }} field holds. Written into the seeded templates as a comment.
FIELD_HELP = {
    'name': _("Recipient's first name (or username)"),
    'org': _('Organization name'),
    'link': _('Main link of the email: the event page, invitation, profile or certificate'),
    'id_code': _("The volunteer's 6-digit kiosk ID"),
    'subject': _('Subject line'),
    'event': _('Event title'),
    'role': _('Role name'),
    'date': _('Date and time of the shift'),
    'location': _('Event location, with the check-in point if there is one'),
    'message': _('Text written by staff, as plain text'),
    'message_html': _('The same text as HTML. It may be empty, so keep the "with ... Safe ... end" wrapper'),
    'from_name': _('Name of the person who gave the endorsement'),
    'skills': _('Endorsed skills, separated by commas'),
    'module': _('Training module title'),
    'certificate': _('Certificate name'),
    'notes': _("Reviewer's notes; may be empty"),
    'body': _('The message as plain text'),
    'html': _('The message as HTML. It may be empty, so keep the "with ... Safe ... end" wrapper'),
}

# What each brand variable at the top of a seeded template holds.
BRAND_HELP = {
    'org_name': _('Organization name'),
    'logo_url': _('Logo, uploaded to listmonk media; empty when there is no logo'),
    'color_primary': _('Primary brand colour (headings)'),
    'color_on_primary': _('Text colour on primary-coloured backgrounds'),
    'color_accent': _('Accent colour (buttons, highlights)'),
    'color_on_accent': _('Text colour on accent-coloured buttons'),
    'color_background': _('Page background around the email card'),
    'color_card': _('Background of the email card'),
    'color_text': _('Body text colour'),
    'color_muted': _('Secondary text colour (footers, notes)'),
    'color_divider': _('Divider and border colour'),
}


def setting(key):
    """(enabled, auto_send, extra_list_ids) for a trigger, using its defaults when unsaved."""
    from .models import EmailTrigger

    trigger = BY_KEY[key]
    row = EmailTrigger.objects.filter(key=key).first()
    if row is None:
        return trigger.default_on, False, []
    return row.enabled or trigger.required, row.auto_send, list(row.extra_list_ids or [])
