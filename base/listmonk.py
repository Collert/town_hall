"""listmonk integration: transactional email and the volunteer mailing list.

Town Hall doesn't build email templates or manage lists itself; listmonk does.
Connection settings are saved in the console (Organization > Backend) and fall back
to LISTMONK_URL / LISTMONK_API_USER / LISTMONK_API_TOKEN.

- On connecting, Town Hall creates the private "Town Hall users" list, uploads the
  logo, and seeds one template per email trigger and language (base/email_templates.py).
  Staff edit those templates in listmonk; identity changes are written back into them.
- Emails go through POST /api/tx (base.email.ListmonkEmailBackend) with the template
  for the trigger and the recipient's language; data is available as {{ .Tx.Data.* }}.
- "New event published" creates a visual campaign to "Town Hall users" and any extra lists.
- Every user is mirrored as a subscriber of "Town Hall users".

API reference: https://listmonk.app/docs/apis/apis/
"""
import json
import mimetypes
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid

TIMEOUT = 15
USER_AGENT = 'TownHall/1.0 (listmonk integration)'
USERS_LIST_NAME = 'Town Hall users'


class ListmonkError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def config():
    """Connection settings: console values first, then environment variables."""
    from .models import SiteSettings

    site = SiteSettings.get_settings()
    return {
        'url': site.backend_value('listmonk_url', 'LISTMONK_URL').rstrip('/'),
        'api_user': site.backend_value('listmonk_api_user', 'LISTMONK_API_USER'),
        'api_token': site.backend_value('listmonk_api_token', 'LISTMONK_API_TOKEN'),
    }


def is_configured(cfg=None):
    return all((cfg or config()).values())


def api(method, path, payload=None, cfg=None, raw=None, content_type='application/json'):
    """Call the listmonk API and return the response's ``data``. ``raw`` sends a
    pre-encoded body (e.g. a multipart upload) instead of JSON."""
    cfg = cfg or config()
    if not is_configured(cfg):
        raise ListmonkError('listmonk is not configured.')
    request = urllib.request.Request(
        f"{cfg['url']}/api{path}",
        data=raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None),
        method=method,
        headers={
            'Authorization': f"token {cfg['api_user']}:{cfg['api_token']}",
            'Content-Type': content_type,
            'Accept': 'application/json',
            # Cloudflare's Browser Integrity Check rejects urllib's default
            # "Python-urllib/x.y" agent with error 1010.
            'User-Agent': USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors='replace')
        try:
            detail = json.loads(detail).get('message', detail)
        except (ValueError, AttributeError):
            pass
        raise ListmonkError(f'listmonk returned {exc.code}: {detail}'[:300], exc.code) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ListmonkError(f"listmonk unreachable at {cfg['url']}: {getattr(exc, 'reason', exc)}") from exc
    return json.loads(body).get('data') if body else None


def _results(data):
    """Paginated endpoints wrap rows in {'results': [...]}; minimal ones return the list."""
    return data.get('results', []) if isinstance(data, dict) else (data or [])


# ---------------------------------------------------------------------------
# Lists and templates
# ---------------------------------------------------------------------------

def get_lists():
    return [(row['id'], row['name']) for row in _results(api('GET', '/lists?minimal=true&per_page=all'))]


def get_templates():
    """Templates by type: {'tx': [(id, name)], 'campaign': [...], 'campaign_visual': [...]}."""
    templates = {'tx': [], 'campaign': [], 'campaign_visual': []}
    for row in _results(api('GET', '/templates?no_body=true')):
        templates.setdefault(row.get('type'), []).append((row['id'], row['name']))
    return templates


def ensure_users_list():
    """Make sure users have a list to sync into: the private "Town Hall users" list,
    reused if it already exists in listmonk, created otherwise. Returns (list_id, created).
    """
    from .models import SiteSettings

    site = SiteSettings.get_settings()
    if site.listmonk_list_id:
        return site.listmonk_list_id, False
    list_id = next((i for i, name in get_lists() if name.strip().lower() == USERS_LIST_NAME.lower()), None)
    created = list_id is None
    if created:
        list_id = api('POST', '/lists', {
            'name': USERS_LIST_NAME, 'type': 'private', 'optin': 'single', 'tags': ['town-hall'],
            'description': 'Everyone with a Town Hall account. Kept in sync by Town Hall.',
        })['id']
    _update_site(listmonk_list_id=list_id)
    return list_id, created


def _update_site(**fields):
    """Save SiteSettings fields without overwriting edits made elsewhere meanwhile."""
    from django.core.cache import cache

    from .models import SiteSettings

    SiteSettings.objects.filter(pk=1).update(**fields)
    cache.delete('site_settings')


def upload_media(filename, content, content_type):
    """Upload a file to listmonk's media library and return its public URL."""
    boundary = uuid.uuid4().hex
    safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', filename) or 'file'
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
        f'Content-Type: {content_type}\r\n\r\n'
    ).encode() + content + f'\r\n--{boundary}--\r\n'.encode()
    return api('POST', '/media', raw=body, content_type=f'multipart/form-data; boundary={boundary}')['url']


def upload_file(field_file):
    """Upload a Django FieldFile (an ImageField value) and return its listmonk URL."""
    with field_file.open('rb') as handle:
        content = handle.read()
    name = os.path.basename(field_file.name)
    return upload_media(name, content, mimetypes.guess_type(name)[0] or 'application/octet-stream')


def sync_logo():
    """Copy the organization logo into listmonk's media library, once per logo file,
    so emails can show it without Town Hall being publicly reachable."""
    from .models import SiteSettings

    site = SiteSettings.get_settings()
    source = site.logo.name if site.logo else ''
    if source == site.listmonk_logo_source and bool(site.listmonk_logo_url) == bool(source):
        return site.listmonk_logo_url
    url = upload_file(site.logo) if source else ''
    _update_site(listmonk_logo_url=url, listmonk_logo_source=source)
    return url


# ---------------------------------------------------------------------------
# Seeded templates (base/email_templates.py)
# ---------------------------------------------------------------------------

def seed_templates(keys=None, overwrite=False):
    """Create the Town Hall templates that are missing in listmonk: one per email
    trigger and enabled language. With ``overwrite`` (Reset to default), re-render them
    in place; a template staff picked themselves is left alone and a fresh one is
    created instead. Returns how many templates were written.
    """
    from . import email_templates, triggers
    from .models import EmailTemplate, SiteSettings

    sync_logo()
    site = SiteSettings.get_settings()
    brand = email_templates.brand_values(site)
    if site.listmonk_brand and site.listmonk_brand != brand:
        refresh_brand()  # bring existing templates up to date first, so all share one brand
    names = {tid: name for rows in get_templates().values() for tid, name in rows}
    written = 0
    for trigger in triggers.TRIGGERS:
        if keys and trigger.key not in keys:
            continue
        for lang in email_templates.languages():
            row = EmailTemplate.objects.filter(trigger=trigger.key, language=lang).first()
            current = names.get(row.template_id) if row else None
            if current and not overwrite:
                continue
            payload = email_templates.render(trigger.key, lang, brand)
            if current and current.startswith(email_templates.NAME_PREFIX):
                api('PUT', f'/templates/{row.template_id}', payload)
            else:
                template_id = api('POST', '/templates', payload)['id']
                EmailTemplate.objects.update_or_create(
                    trigger=trigger.key, language=lang, defaults={'template_id': template_id})
            written += 1
    _update_site(listmonk_brand=brand)
    return written


def refresh_brand():
    """Write the organization's current name, logo and colours into every template the
    email triggers use. Returns how many templates changed."""
    from . import email_templates
    from .models import EmailTemplate, SiteSettings

    sync_logo()
    site = SiteSettings.get_settings()
    brand = email_templates.brand_values(site)
    old = site.listmonk_brand or {}
    changed = 0
    for template_id in set(EmailTemplate.objects.values_list('template_id', flat=True)):
        try:
            template = api('GET', f'/templates/{template_id}')
        except ListmonkError as exc:
            if exc.status == 404:
                continue  # deleted in listmonk; the Communication page offers to re-create it
            raise
        body, source = template.get('body') or '', template.get('body_source') or ''
        if template.get('type') == 'campaign_visual':
            new_body = email_templates.swap_brand(body, old, brand)
            new_source = email_templates.swap_brand(source, old, brand, as_json=True)
        elif email_templates.BRAND_MARKER in body:
            new_body, new_source = email_templates.upgrade_body(email_templates.apply_brand(body, brand)), source
        else:
            continue
        if (new_body, new_source) == (body, source):
            continue
        payload = {key: template.get(key) for key in ('name', 'type', 'subject')}
        payload['body'] = new_body
        if new_source:
            payload['body_source'] = new_source
        api('PUT', f'/templates/{template_id}', payload)
        changed += 1
    _update_site(listmonk_brand=brand)
    return changed


def connect():
    """Everything listmonk needs from Town Hall: the users list, the logo, the seeded
    templates, and (for a new list) every existing user. Safe to run repeatedly."""
    _list_id, list_created = ensure_users_list()
    seeded = seed_templates()
    synced = sync_all()[0] if list_created else 0
    return f'{seeded} template(s) created, {synced} user(s) added'


# ---------------------------------------------------------------------------
# Background jobs
# ---------------------------------------------------------------------------

TASK_KEY = 'listmonk_task'
TASK_ERROR_KEY = 'listmonk_task_error'


def run_in_background(label, job, *args):
    """Run a slow listmonk job in a thread. While it runs, ``current_task()`` returns
    ``label`` so the console can say so; a failure is kept for ``last_error()``."""
    from django.core.cache import cache

    cache.set(TASK_KEY, str(label), 15 * 60)
    cache.delete(TASK_ERROR_KEY)

    def run():
        from django.db import connection

        try:
            print(f'[listmonk] {label}: {job(*args)}')
        except ListmonkError as exc:
            print(f'[listmonk] {label} failed: {exc}')
            cache.set(TASK_ERROR_KEY, str(exc), 60 * 60)
        finally:
            cache.delete(TASK_KEY)
            connection.close()

    threading.Thread(target=run, daemon=True).start()


def current_task():
    from django.core.cache import cache

    return cache.get(TASK_KEY)


def last_error():
    from django.core.cache import cache

    return cache.get(TASK_ERROR_KEY)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def send_tx(template_id, recipients, data, from_email=None, altbody=None):
    """Send a transactional message to each recipient with a template's own subject.

    ``fallback`` mode delivers to addresses that aren't subscribers yet (e.g. a
    password reset before the account was synced).
    """
    payload = {
        'subscriber_emails': list(recipients),
        'subscriber_mode': 'fallback',
        'template_id': template_id,
        'data': data,
        'content_type': 'html',
    }
    if from_email:
        payload['from_email'] = from_email
    if altbody:
        payload['altbody'] = altbody
    api('POST', '/tx', payload)


def create_visual_campaign(name, subject, body, body_source, list_ids, send=False):
    """Create a visual campaign (editable in listmonk's block editor), and start it
    when ``send``. Returns its ID."""
    from .models import SiteSettings

    site = SiteSettings.get_settings()
    payload = {
        'name': name, 'subject': subject, 'lists': list_ids, 'type': 'regular',
        'content_type': 'visual', 'body': body, 'body_source': body_source, 'tags': ['town-hall'],
    }
    if site.default_from_email:
        payload['from_email'] = site.default_from_email
    campaign = api('POST', '/campaigns', payload)
    if send:
        api('PUT', f"/campaigns/{campaign['id']}/status", {'status': 'running'})
    return campaign['id']


# ---------------------------------------------------------------------------
# Subscriber sync
# ---------------------------------------------------------------------------

def subscriber_attribs(user):
    attribs = {'town_hall_id': user.pk, 'username': user.username, 'staff': user.is_staff}
    profile = getattr(user, 'profile', None)
    if profile:
        level = profile.level
        attribs.update(impact_points=profile.impact_points, level=level.name if level else '',
                       language=profile.language)
    return attribs


def find_subscriber(user):
    """The listmonk subscriber for ``user``: matched by Town Hall ID first, so an email
    change updates the same subscriber, then by email."""
    email = user.email.replace("'", "''")
    query = f"subscribers.attribs->>'town_hall_id' = '{user.pk}' OR subscribers.email = '{email}'"
    rows = _results(api('GET', '/subscribers?' + urllib.parse.urlencode({'query': query, 'per_page': 2})))
    return next((r for r in rows if (r.get('attribs') or {}).get('town_hall_id') == user.pk), rows[0] if rows else None)


def sync_user(user, list_id=None):
    """Create or update ``user``'s subscriber and make sure it's on the volunteer list.

    Existing list memberships are never touched, so someone who unsubscribed from the
    list in listmonk stays unsubscribed.
    """
    from .models import SiteSettings

    if not user.email or not user.is_active:
        return
    list_id = list_id or SiteSettings.get_settings().listmonk_list_id
    name = user.get_full_name() or user.username
    existing = find_subscriber(user)
    if existing:
        api('PATCH', f"/subscribers/{existing['id']}", {
            'email': user.email, 'name': name,
            'attribs': {**(existing.get('attribs') or {}), **subscriber_attribs(user)},
        })
        if list_id and list_id not in {lst['id'] for lst in existing.get('lists') or []}:
            api('PUT', '/subscribers/lists', {
                'ids': [existing['id']], 'action': 'add', 'target_list_ids': [list_id], 'status': 'confirmed',
            })
        return
    api('POST', '/subscribers', {
        'email': user.email, 'name': name, 'status': 'enabled',
        'lists': [list_id] if list_id else [], 'attribs': subscriber_attribs(user),
        'preconfirm_subscriptions': True,
    })


def sync_all():
    """Sync every active user with an email. Returns (synced, failed, first error)."""
    from django.contrib.auth.models import User

    synced, failed, first_error = 0, 0, None
    for user in User.objects.filter(is_active=True).exclude(email='').select_related('profile'):
        try:
            sync_user(user)
            synced += 1
        except ListmonkError as exc:
            failed += 1
            first_error = first_error or exc
            if exc.status in (None, 401, 403):  # unreachable or bad credentials: the rest will fail too
                break
    return synced, failed, first_error


def queue_sync(user):
    """Sync ``user`` in the background after the current transaction commits, so a slow
    or offline listmonk never holds up sign-up or profile saves."""
    if not is_configured():
        return
    from django.db import transaction

    transaction.on_commit(lambda: threading.Thread(target=_sync_in_background, args=(user.pk,), daemon=True).start())


def _sync_in_background(user_pk):
    from django.contrib.auth.models import User
    from django.db import connection

    try:
        user = User.objects.select_related('profile').filter(pk=user_pk).first()
        if user:
            sync_user(user)
    except ListmonkError as exc:
        print(f'[listmonk] Could not sync user {user_pk}: {exc}')
    finally:
        connection.close()
