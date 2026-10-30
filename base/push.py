"""Web push notifications for the installed app and the browser.

Signed-in volunteers turn notifications on per device, from the prompt in the site
layout or from Account settings (base/static/base/push.js); each device is a
``PushSubscription``. Town Hall signs pushes with its own VAPID keys, made on first use
and kept on SiteSettings. The service worker (base/templates/base/sw.js) shows them and
opens the link when one is clicked.

- App events: ``notify(key, user, data)`` for the CATEGORIES below, each switched on or
  off in Console > Communication > Notifications. ``base.email.notify`` calls it with the
  email's data, so an emailed event that has a category here also pushes; leadership
  changes and new events call it directly.
- listmonk campaigns: Town Hall is a listmonk messenger
  (https://listmonk.app/docs/messengers/). Each user's push endpoints are kept in their
  subscriber attribute ``town_hall_webpush`` (base.listmonk.subscriber_attribs). A
  campaign sent with the "townhall" messenger is posted to ``/push/listmonk/`` once per
  recipient, and ``deliver_campaign`` pushes its subject and body to the endpoints in
  that attribute. The "Town Hall push notification" campaign template wraps the click
  link in TrackLink, so listmonk counts clicks, and passes TrackView's pixel, which the
  service worker loads when the notification is shown.
"""
import base64
import html
import json
import re
import secrets
from dataclasses import dataclass

from django.conf import settings
from django.utils import translation
from django.utils.translation import gettext_lazy as _

ATTRIB = 'town_hall_webpush'
MESSENGER_NAME = 'townhall'
MESSENGER_USER = 'townhall'
TEMPLATE_NAME = 'Town Hall push notification'
TEMPLATE_MARKER = '[[town-hall-push]]'
TTL = 24 * 60 * 60  # seconds a push service keeps an undelivered message
TITLE_MAX, BODY_MAX = 120, 300
# Browser push services (Chrome/Edge/Samsung/Opera use FCM or WNS, Firefox Mozilla's,
# Safari Apple's). Only endpoints on these hosts are accepted, so a signed-in user can't
# make the server post to an address of their choosing.
PUSH_SERVICE_HOSTS = ('fcm.googleapis.com', 'android.googleapis.com', 'push.services.mozilla.com',
                      'notify.windows.com', 'push.apple.com')


@dataclass(frozen=True)
class Category:
    key: str
    icon: str
    label: str
    description: str
    audience: str
    title: str = ''   # %(field)s placeholders, filled from the event's data
    body: str = ''
    default_on: bool = True


CATEGORIES = [
    Category('event_published', 'campaign', _('New event published'),
             _('An event becomes visible to volunteers.'), _('Everyone with notifications on'),
             _('New opportunity: %(event)s'), '%(date)s · %(location)s'),
    Category('shift_reminder', 'alarm', _('Shift reminder'),
             _("At 9 AM the day before a volunteer's shift, like the reminder email."),
             _('Each volunteer signed up for the shift'),
             _('Tomorrow: %(role)s at %(event)s'), '%(date)s · %(location)s'),
    Category('invitation', 'mail', _('Invitation received'),
             _('Staff invite a volunteer to a role from the console.'), _('The invited volunteer'),
             _("You're invited: %(role)s"), '%(event)s · %(date)s'),
    Category('leadership', 'military_tech', _('Leadership'),
             _('Someone is made a shift lead or area lead, or shift leads are picked automatically.'),
             _('The new lead, or the general coordinators'), '%(event)s', '%(message)s'),
    Category('event_message', 'forum', _('Message to event volunteers'),
             _('Staff use "Message All Volunteers" on an event.'), _("The event's volunteers"),
             '%(event)s', '%(message)s'),
    Category('volunteer_message', 'chat', _('Message to a volunteer'),
             _("Staff send a message from a volunteer's profile."), _('That volunteer'),
             _('Message from %(org)s'), '%(message)s'),
    Category('endorsement_received', 'thumb_up', _('Endorsement received'),
             _('Someone endorses a volunteer for a skill.'), _('The endorsed volunteer'),
             _('%(from_name)s endorsed you'), '%(skills)s'),
    Category('shift_thanks', 'volunteer_activism', _('Thank you after a shift'),
             _('A volunteer clocks out of their last shift at an event.'), _('The volunteer'),
             _('Thank you for volunteering at %(event)s'), _('%(hours)s hours as %(role)s. Tell us how it went.')),
    Category('certificate_approved', 'verified', _('Certificate approved'),
             _('Staff verify an uploaded certificate.'), _('The volunteer'),
             _('Certificate verified'), '%(certificate)s'),
    Category('certificate_rejected', 'assignment_return', _('Certificate rejected'),
             _('Staff send a certificate back for another upload.'), _('The volunteer'),
             _('Your certificate needs another look'), '%(certificate)s'),
    Category('campaign', 'outgoing_mail', _('listmonk campaigns'),
             _('Plain-text campaigns sent from listmonk with the Town Hall messenger.'),
             _("Subscribers of the campaign's lists who turned notifications on")),
]

BY_KEY = {c.key: c for c in CATEGORIES}


class _Blank(dict):
    """Format data where a missing field reads as empty instead of raising."""
    def __missing__(self, key):
        return ''


def is_push_service(url):
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    return parts.scheme == 'https' and any(host == h or host.endswith('.' + h) for h in PUSH_SERVICE_HOSTS)


def is_on(key):
    from .models import SiteSettings

    saved = SiteSettings.get_settings().push_categories or {}
    return bool(saved.get(key, BY_KEY[key].default_on))


# ---------------------------------------------------------------------------
# Keys and credentials (made on first use)
# ---------------------------------------------------------------------------

def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def _claim(field, make):
    """Fill an empty SiteSettings field once, even with several workers racing."""
    from django.core.cache import cache

    from .models import SiteSettings

    site = SiteSettings.get_settings()
    if not getattr(site, field):
        SiteSettings.objects.filter(pk=1, **{field: ''}).update(**make())
        cache.delete('site_settings')
        site = SiteSettings.get_settings()
    return site


def public_key():
    """The VAPID application server key browsers subscribe with (base64url)."""
    def make():
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid

        vapid = Vapid()
        vapid.generate_keys()
        point = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return {'vapid_private_key': vapid.private_pem().decode(), 'vapid_public_key': _b64url(point)}

    return _claim('vapid_private_key', make).vapid_public_key


def messenger_password():
    """The password listmonk's messenger signs in to /push/listmonk/ with."""
    return _claim('push_messenger_password', lambda: {'push_messenger_password': secrets.token_urlsafe(24)}).push_messenger_password


def _vapid():
    from py_vapid import Vapid

    from .models import SiteSettings

    public_key()
    return Vapid.from_pem(SiteSettings.get_settings().vapid_private_key.encode())


def _subject():
    """Contact for push services (Apple rejects pushes without a real one)."""
    from django.contrib.auth.models import User

    from .models import SiteSettings

    sender = SiteSettings.get_settings().default_from_email
    match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', sender or '')
    email = match.group(0) if match else (
        User.objects.filter(is_superuser=True).exclude(email='').values_list('email', flat=True).first())
    return f'mailto:{email or "webmaster@town-hall.app"}'


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def send(subscription, payload):
    """Push ``payload`` ({title, body, url, ...}) to one device. A device the push service
    no longer knows (404/410) is forgotten. Returns True when the service accepted it."""
    from pywebpush import WebPushException, webpush

    from . import listmonk

    try:
        webpush(
            subscription_info={'endpoint': subscription.endpoint,
                               'keys': {'p256dh': subscription.p256dh, 'auth': subscription.auth}},
            data=json.dumps(payload),
            vapid_private_key=_vapid(),
            vapid_claims={'sub': _subject()},
            ttl=TTL,
            timeout=10,
        )
        return True
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in (404, 410):
            user = subscription.user
            subscription.delete()
            listmonk.queue_sync(user)
        else:
            print(f'[push] {subscription.endpoint[:60]}… failed: {exc}')
    except Exception as exc:  # network errors vary (requests, ssl, bad keys)
        print(f'[push] {subscription.endpoint[:60]}… failed: {exc}')
    return False


def message(key, data, lang):
    """The {title, body, url} for a category in ``lang``."""
    from .models import SiteSettings

    category = BY_KEY[key]
    values = _Blank({'org': SiteSettings.get_settings().company_name, **data})
    with translation.override(lang):
        title = str(category.title) % values
        body = str(category.body) % values
    body = ' · '.join(part for part in (p.strip() for p in body.split(' · ')) if part)  # drop empty fields
    return {'title': title.strip()[:TITLE_MAX], 'body': body[:BODY_MAX], 'url': data.get('link') or '/'}


def _language(user):
    profile = getattr(user, 'profile', None)
    lang = profile.language if profile else ''
    return lang if lang in dict(settings.LANGUAGES) else settings.LANGUAGE_CODE


def run_after_commit(job, *args):
    """Run ``job(*args)`` in a thread once the transaction commits, so push services never
    hold up a request. With settings.PUSH_RUN_INLINE (tests) it runs in the commit hook."""
    import threading

    from django.db import transaction

    def run():
        from django.db import connection
        try:
            job(*args)
        except Exception as exc:
            print(f'[push] {getattr(job, "__name__", job)} failed: {exc}')
        finally:
            if not getattr(settings, 'PUSH_RUN_INLINE', False):
                connection.close()

    if getattr(settings, 'PUSH_RUN_INLINE', False):
        transaction.on_commit(run)
    else:
        transaction.on_commit(lambda: threading.Thread(target=run, daemon=True).start())


def notify(key, user, data=None):
    """Push an app event to every device ``user`` turned notifications on for, if its
    category is on. ``data`` is the event's email data; ``link`` is where a click goes."""
    from .models import PushSubscription

    if key not in BY_KEY or not user.is_active or not is_on(key):
        return
    if not PushSubscription.objects.filter(user=user).exists():
        return
    run_after_commit(_push_to_user, key, user.pk, dict(data or {}))


def _push_to_user(key, user_pk, data):
    from .models import PushSubscription

    subscriptions = list(PushSubscription.objects.filter(user_id=user_pk).select_related('user__profile'))
    if subscriptions:
        payload = message(key, data, _language(subscriptions[0].user))
        for subscription in subscriptions:
            send(subscription, payload)


def announce_event(event, base_url):
    """'New event published' to everyone with notifications on, each in their language."""
    from .models import PushSubscription

    if not is_on('event_published') or not PushSubscription.objects.filter(user__is_active=True).exists():
        return
    run_after_commit(_push_event, event.pk, base_url)


def _push_event(event_pk, base_url):
    from django.urls import reverse

    from events.models import Event

    from .models import PushSubscription
    from .utils import short_datetime

    event = Event.objects.select_related('venue').filter(pk=event_pk, published=True).first()
    if not event:
        return
    payloads = {}
    for subscription in PushSubscription.objects.filter(user__is_active=True).select_related('user__profile'):
        lang = _language(subscription.user)
        if lang not in payloads:
            with translation.override(lang):
                link = base_url.rstrip('/') + reverse('opportunity_detail', args=[event.pk])
            payloads[lang] = message('event_published', {
                'event': event.title, 'date': short_datetime(event.start_date, lang),
                'location': event.place_name, 'link': link,
            }, lang)
        send(subscription, payloads[lang])


def send_test(user):
    """Push a test notification to ``user``'s devices now. Returns how many accepted it."""
    from .models import PushSubscription, SiteSettings

    with translation.override(_language(user)):
        payload = {'title': SiteSettings.get_settings().company_name,
                   'body': str(_('Notifications work on this device.')), 'url': '/'}
    return sum(send(s, payload) for s in PushSubscription.objects.filter(user=user))


# ---------------------------------------------------------------------------
# listmonk messenger
# ---------------------------------------------------------------------------

URL_RE = re.compile(r'https?://[^\s<>"\']+')


def parse_campaign_body(body, content_type='plain'):
    """Split a rendered campaign body into (text, click link, view pixel URL).

    With the Town Hall template, the part after TEMPLATE_MARKER carries the tracked
    default link and TrackView's pixel. The first link in the message itself (e.g.
    ``https://example.org/en/opportunities/@TrackLink``) wins over the default and is
    taken out of the text."""
    text, _sep, extra = (body or '').partition(TEMPLATE_MARKER)
    if content_type != 'plain':
        text = re.sub(r'(?i)<br\s*/?>|</p>|</div>|</h\d>|</li>', '\n', text)
        text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    default = re.search(r'^link:\s*(\S+)', extra, re.M)
    view = re.search(r'src="([^"]+)"', extra)
    link = default and html.unescape(default.group(1))
    found = URL_RE.search(text)
    if found:
        link = found.group(0).rstrip('.,;:!?)')
        text = text.replace(link, '', 1)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\s*\n\s*', '\n', text).strip()
    return text, link, view and html.unescape(view.group(1))


def deliver_campaign(payload, default_url='/'):
    """Push a campaign message listmonk posted to the messenger. Each recipient's
    ``town_hall_webpush`` attribute lists their endpoints; only devices Town Hall knows,
    belonging to that subscriber, are pushed. Returns how many pushes were accepted."""
    from django.db.models import Q

    from .models import PushSubscription

    if not is_on('campaign'):
        return 0
    text, link, view = parse_campaign_body(payload.get('body') or '', payload.get('content_type') or 'plain')
    note = {'title': (payload.get('subject') or '').strip()[:TITLE_MAX], 'body': text[:BODY_MAX],
            'url': link or default_url}
    if view:
        note['view'] = view
    sent = 0
    for recipient in payload.get('recipients') or []:
        attribs = recipient.get('attribs') or {}
        endpoints = attribs.get(ATTRIB) or []
        endpoints = [e for e in ([endpoints] if isinstance(endpoints, str) else endpoints) if isinstance(e, str)]
        if not endpoints:
            continue
        owner = Q(user__email__iexact=recipient.get('email') or '-')
        if str(attribs.get('town_hall_id', '')).isdigit():
            owner |= Q(user_id=int(attribs['town_hall_id']))
        for subscription in PushSubscription.objects.filter(owner, endpoint__in=endpoints, user__is_active=True):
            sent += send(subscription, note)
    return sent


def template_body(home_url):
    """The "Town Hall push notification" campaign template (Go template syntax)."""
    return (
        '{{- /* Town Hall push notification. Use it for plain-text campaigns sent with the '
        '"townhall" messenger. The subject is the title and the message the text. To open a '
        'page when the notification is clicked, put its address on the last line with '
        '"@TrackLink" right after it (no space) so clicks are counted; without one, the '
        'link below opens. Keep everything from the marker down. */ -}}\n'
        '{{ template "content" . }}\n'
        f'{TEMPLATE_MARKER}\n'
        f'link: {{{{ TrackLink "{home_url}" }}}}\n'
        'view: {{ TrackView }}\n'
    )


def _masked(value):
    """listmonk shows saved passwords as bullets; sending "" back keeps them."""
    if isinstance(value, dict):
        return {k: _masked(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_masked(v) for v in value]
    if isinstance(value, str) and value and set(value) == {'•'}:
        return ''
    return value


def listmonk_status(messenger_url):
    """{'template': name or None, 'messenger': True/False/None (can't tell)} for the console."""
    from . import listmonk
    from .models import SiteSettings

    site = SiteSettings.get_settings()
    status = {'template': None, 'messenger': None}
    if site.listmonk_push_template_id:
        status['template'] = dict(listmonk.get_templates().get('campaign', [])).get(site.listmonk_push_template_id)
    try:
        messengers = listmonk.api('GET', '/settings').get('messengers') or []
    except listmonk.ListmonkError:
        return status
    status['messenger'] = any(m.get('name') == MESSENGER_NAME and m.get('enabled')
                              and m.get('root_url') == messenger_url for m in messengers)
    return status


def setup_listmonk(messenger_url, home_url):
    """Create (or refresh) the campaign template and register Town Hall as the "townhall"
    messenger in listmonk's settings. Saving settings makes listmonk reload. Raises
    ListmonkError; the template is kept even if the messenger step fails."""
    from . import listmonk
    from .models import SiteSettings

    site = SiteSettings.get_settings()
    template = {'name': TEMPLATE_NAME, 'type': 'campaign', 'body': template_body(home_url)}
    existing = dict(listmonk.get_templates().get('campaign', []))
    if site.listmonk_push_template_id in existing:
        listmonk.api('PUT', f'/templates/{site.listmonk_push_template_id}', template)
    else:
        template_id = listmonk.api('POST', '/templates', template)['id']
        listmonk._update_site(listmonk_push_template_id=template_id)

    current = _masked(listmonk.api('GET', '/settings'))
    messengers = [m for m in current.get('messengers') or [] if m.get('name') != MESSENGER_NAME]
    old = next((m for m in current.get('messengers') or [] if m.get('name') == MESSENGER_NAME), {})
    messengers.append({
        'uuid': old.get('uuid', ''), 'enabled': True, 'name': MESSENGER_NAME, 'root_url': messenger_url,
        'username': MESSENGER_USER, 'password': messenger_password(),
        'max_conns': old.get('max_conns') or 10, 'timeout': old.get('timeout') or '10s',
        'max_msg_retries': old.get('max_msg_retries') or 2,
    })
    current['messengers'] = messengers
    listmonk.api('PUT', '/settings', current)
