"""The actions Switchboard can ask Town Hall to run (the manifest's `actions`).

Each takes the connection, the checked params and the request, and returns the JSON object
the manifest's `output` describes. `ActionError` becomes the protocol's error body; its
message is shown to a non-technical admin in Switchboard's run log.

People are matched by email, ignoring case.
"""
import secrets

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from . import events


class ActionError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def invalid(message):
    return ActionError(422, 'invalid_params', message)


def person_output(user):
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
    }


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


def _person(params):
    person_id = params['person_id'].strip()
    user = User.objects.filter(pk=int(person_id)).first() if person_id.isdigit() else None
    return user


def person_find(connection, params, request):
    user = find_by_email(_email(params))
    return person_output(user) if user else {'found': False}


def person_get(connection, params, request):
    user = _person(params)
    return person_output(user) if user else {'found': False}


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


def notification_send(connection, params, request):
    """A notification like the one staff send from a volunteer's page in the console: in the
    site's bell, and by email and push where those are switched on."""
    from base.email import notify, text_to_html
    from base.models import Notification

    user = _person(params)
    if not user:
        raise invalid(f'There is no volunteer with person ID "{params["person_id"]}".')
    if not user.is_active:
        raise ActionError(409, 'conflict', f"{events.person_name(user)}'s account is deactivated, so they can't be notified.")
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


HANDLERS = {
    'person.find': person_find,
    'person.get': person_get,
    'person.invite': person_invite,
    'notification.send': notification_send,
}
