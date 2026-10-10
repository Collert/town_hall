"""Bell Tower integration: shared task lists for events.

Bell Tower is the organization's shared to-do service (lists hold tasks; people,
pager devices and AI agents work through them). Town Hall keeps no tasks itself: an
event's lists live in Bell Tower and `events.EventTaskList` only remembers their IDs.

Connecting (console > Organization > Backend) is an OAuth-style handshake, so nobody
copies keys around:

1. Staff enter the Bell Tower address; Town Hall reads ``/.well-known/belltower``.
2. The browser goes to Bell Tower's authorize page with our callback URL and a random
   ``state`` (kept in the session). The user signs in there and approves.
3. Bell Tower redirects back with ``code`` + ``state``; Town Hall checks the state and
   trades the code for a long-lived API key at the token endpoint (server to server).

Town Hall then acts as the Bell Tower user who approved. Lists it creates are
``persistent`` (exempt from Bell Tower's automatic cleanup) and include that user, so
sharing a list must keep them as a member.

People: every Town Hall user with an email is linked to the Bell Tower account with
that email (``base.BellTowerLink``), which Bell Tower creates when missing. That needs
the connected account to be Bell Tower staff. Linked people open Bell Tower through
one-time sign-in links (``login_link``), so they never need a Bell Tower password.
"""
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from django.utils.translation import gettext as _

TIMEOUT = 10
USER_AGENT = 'TownHall/1.0 (Bell Tower integration)'
CLIENT_NAME = 'Town Hall'


class BellTowerError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def normalize_url(raw):
    """The server's origin from whatever was typed: scheme + host (+ port), nothing else.

    "tasks.example.org/lists/3/" -> "https://tasks.example.org". A missing scheme means
    https, except for localhost-style addresses, where http is the likely choice.
    """
    raw = (raw or '').strip()
    if not raw:
        raise ValueError(_('Enter the address of your Bell Tower server.'))
    if '://' not in raw:
        host = (urllib.parse.urlsplit('//' + raw).hostname or '').lower()
        local = host in ('localhost', '127.0.0.1', '0.0.0.0', '::1') or host.endswith('.local')
        raw = ('http://' if local else 'https://') + raw
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError(_("That doesn't look like a web address."))
    try:
        port = parts.port
    except ValueError:
        raise ValueError(_('That address has an invalid port.'))
    host = parts.hostname.lower()
    if ':' in host:  # IPv6 literal
        host = f'[{host}]'
    return f'{parts.scheme}://{host}' + (f':{port}' if port else '')


def _error_text(body):
    """Bell Tower's (DRF's) error bodies as one line: {"detail": …}, ["…"], or {"field": ["…"]}."""
    if isinstance(body, dict):
        if 'detail' in body:
            return str(body['detail'])
        return ' '.join(_error_text(value) for value in body.values() if isinstance(value, (list, dict, str)))
    if isinstance(body, list):
        return ' '.join(_error_text(item) for item in body)
    return str(body)


def _http(method, url, *, data=None, headers=None):
    request = urllib.request.Request(url, data=data, method=method, headers={
        'Accept': 'application/json',
        'User-Agent': USER_AGENT,
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors='replace')
        try:
            detail = _error_text(json.loads(detail))
        except ValueError:
            pass
        raise BellTowerError(f'Bell Tower returned {exc.code}: {detail}'[:300], exc.code) from exc
    except (urllib.error.URLError, OSError) as exc:
        host = urllib.parse.urlsplit(url).netloc
        raise BellTowerError(f'Bell Tower unreachable at {host}: {getattr(exc, "reason", exc)}') from exc
    if not body:
        return None
    try:
        return json.loads(body)
    except ValueError:
        raise BellTowerError('Bell Tower sent a response that isn\'t JSON. Is that the right address?')


# ---------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------

ENDPOINT_KEYS = ('authorize_url', 'token_url', 'api_url')


def discover(base_url):
    """Read the server's /.well-known/belltower and return its endpoints."""
    data = _http('GET', f'{base_url}/.well-known/belltower')
    if not isinstance(data, dict) or data.get('service') != 'belltower' or not all(data.get(k) for k in ENDPOINT_KEYS):
        raise BellTowerError(_('No Bell Tower server answered at that address.'))
    # The code and key are sent to these endpoints, so they must stay on the server the
    # user named. Any that point elsewhere (e.g. Bell Tower behind a proxy that doesn't
    # know its public host, or that terminates TLS and advertises http://) are rebuilt
    # on that server.
    base = urllib.parse.urlsplit(base_url)
    for key in (*ENDPOINT_KEYS, 'revoke_url', 'list_url', 'mcp_url'):
        parts = urllib.parse.urlsplit(data.get(key) or '')
        if parts.netloc and (parts.netloc, parts.scheme) != (base.netloc, base.scheme):
            data[key] = base_url + parts.path + (f'?{parts.query}' if parts.query else '')
    return {key: data.get(key, '') for key in (*ENDPOINT_KEYS, 'revoke_url', 'list_url', 'mcp_url')}


def authorize_url(endpoints, redirect_uri, state):
    query = urllib.parse.urlencode({'client_name': CLIENT_NAME, 'redirect_uri': redirect_uri, 'state': state})
    return f"{endpoints['authorize_url']}?{query}"


def exchange_code(endpoints, code, redirect_uri):
    """Trade the one-time code for an API key. Returns {'api_key', 'username'}."""
    data = _http(
        'POST', endpoints['token_url'],
        data=urllib.parse.urlencode({'code': code, 'redirect_uri': redirect_uri}).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    if not isinstance(data, dict) or not data.get('api_key'):
        raise BellTowerError('Bell Tower didn\'t return an API key.')
    return {'api_key': data['api_key'], 'username': data.get('username', '')}


def save_connection(base_url, endpoints, api_key, username):
    from django.utils import timezone

    from .models import SiteSettings

    site = SiteSettings.get_settings()
    site.belltower_url = base_url
    site.belltower_endpoints = endpoints
    site.belltower_api_key = api_key
    site.belltower_username = username
    site.belltower_connected_at = timezone.now()
    site.save()


def disconnect():
    """Forget the connection, revoking our key in Bell Tower when it's reachable.
    Returns False when the key couldn't be revoked there (the user can do it on their
    Bell Tower account page)."""
    from .models import SiteSettings

    site = SiteSettings.get_settings()
    revoked = True
    revoke_url = site.belltower_endpoints.get('revoke_url') if site.belltower_endpoints else ''
    if site.belltower_api_key and revoke_url:
        try:
            _http('POST', revoke_url, headers={'Authorization': f'Bearer {site.belltower_api_key}'})
        except BellTowerError as exc:
            revoked = exc.status == 401  # already revoked counts as done
    site.belltower_api_key = ''
    site.belltower_username = ''
    site.belltower_connected_at = None
    site.save()
    return revoked


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

def config():
    from .models import SiteSettings

    site = SiteSettings.get_settings()
    return {
        'url': site.belltower_url,
        'api_key': site.belltower_api_key,
        'username': site.belltower_username,
        'endpoints': site.belltower_endpoints or {},
    }


def is_connected(cfg=None):
    cfg = cfg or config()
    return bool(cfg['url'] and cfg['api_key'])


def api(method, path, payload=None, params=None, cfg=None):
    """Call Bell Tower's REST API; ``path`` is relative to /api/ (e.g. 'lists/3/')."""
    cfg = cfg or config()
    if not is_connected(cfg):
        raise BellTowerError('Bell Tower is not connected.')
    base = cfg['endpoints'].get('api_url') or f"{cfg['url']}/api/"
    url = base + path + (f'?{urllib.parse.urlencode(params)}' if params else '')
    return _http(
        method, url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': f"Bearer {cfg['api_key']}", 'Content-Type': 'application/json'},
    )


def list_web_url(list_id, cfg=None):
    """Bell Tower's own page for a list, for "Open in Bell Tower"."""
    cfg = cfg or config()
    template = cfg['endpoints'].get('list_url') or f"{cfg['url']}/lists/{{id}}/"
    return template.replace('{id}', str(list_id))


def create_list(name, users=()):
    """Create a persistent list that the connected user (and ``users``) belong to."""
    return api('POST', 'lists/', {'name': name, 'persistent': True, 'users': list(users)})


def get_list(list_id):
    return api('GET', f'lists/{list_id}/')


def update_list(list_id, **fields):
    return api('PATCH', f'lists/{list_id}/', fields)


def delete_list(list_id):
    api('DELETE', f'lists/{list_id}/')


def merge_lists(target_id, source_id, name=None):
    """Fold the source list's tasks and members into the target, which takes ``name``
    (default "Target & Source"). Bell Tower deletes the source."""
    return api('POST', f'lists/{target_id}/merge/', {'list': source_id, **({'name': name} if name else {})})


def move_task(task_id, list_id):
    """Move a task (with its subtasks) to another list."""
    return api('PATCH', f'tasks/{task_id}/', {'list': list_id})


def add_member(list_id, email=None, admin=None, username=None):
    """Share a list with whoever uses ``email`` in Bell Tower, or change a member's role
    (by email or ``username``). ``admin=None`` adds without changing an existing member's
    role. Raises BellTowerError with status 404 when no Bell Tower account matches."""
    who = {'email': email} if email else {'username': username}
    return api('POST', f'lists/{list_id}/members/', {**who, **({} if admin is None else {'admin': admin})})


def remove_member(list_id, username):
    return api('DELETE', f'lists/{list_id}/members/{urllib.parse.quote(username)}/')


def remove_member_if_plain(list_id, username):
    """Take someone off a list unless they're one of its admins (staff-managed)."""
    members = {m['username']: m for m in get_list(list_id).get('members', [])}
    if username in members and not members[username]['admin']:
        remove_member(list_id, username)


def get_tasks(list_id):
    """Every task in a list (completed ones too), following pagination."""
    tasks, page = [], 1
    while True:
        data = api('GET', 'tasks/', params={'list': list_id, 'page': page})
        tasks.extend(data.get('results', []))
        if not data.get('next'):
            return tasks
        page += 1


def create_task(list_id, title, description='', expires_at=None, assignee=None, parent=None):
    """``expires_at`` is an aware datetime; ``assignee`` a member's Bell Tower username."""
    return api('POST', 'tasks/', {
        'list': list_id, 'title': title, 'description': description,
        'expires_at': expires_at.isoformat() if expires_at else None, 'assignee': assignee, 'parent': parent,
    })


def update_task(task_id, **fields):
    if fields.get('expires_at'):
        fields['expires_at'] = fields['expires_at'].isoformat()
    return api('PATCH', f'tasks/{task_id}/', fields)


def delete_task(task_id):
    api('DELETE', f'tasks/{task_id}/')



# ---------------------------------------------------------------------------
# Background work
# ---------------------------------------------------------------------------

def run_after_commit(job, *args):
    """Run ``job(*args)`` in a thread once the current transaction commits, so a slow or
    offline Bell Tower never holds up a sign-up or registration. Failures are printed to
    the server log. With settings.BELLTOWER_RUN_INLINE (tests) it runs in the commit hook."""
    from django.conf import settings
    from django.db import transaction

    def run():
        from django.db import connection
        try:
            job(*args)
        except BellTowerError as exc:
            print(f'[belltower] {getattr(job, "__name__", job)} failed: {exc}')
        finally:
            if not getattr(settings, 'BELLTOWER_RUN_INLINE', False):
                connection.close()

    if getattr(settings, 'BELLTOWER_RUN_INLINE', False):
        transaction.on_commit(run)
    else:
        transaction.on_commit(lambda: threading.Thread(target=run, daemon=True).start())


# ---------------------------------------------------------------------------
# People: linked accounts and sign-in links
# ---------------------------------------------------------------------------

def me():
    """The Bell Tower account Town Hall acts as: {'username', 'email', 'is_staff', ...}."""
    return api('GET', 'me/')


def linked_username(user, create=True):
    """``user``'s Bell Tower username, linking (and, in Bell Tower, creating) the account
    by email when it isn't linked yet. None without a connection or an email."""
    from .models import BellTowerLink

    cfg = config()
    if not is_connected(cfg) or not user.email:
        return None
    link = BellTowerLink.objects.filter(user=user, belltower_url=cfg['url']).first()
    if link or not create:
        return link.username if link else None
    data = api('POST', 'users/link/', {'email': user.email, 'first_name': user.first_name, 'last_name': user.last_name}, cfg=cfg)
    link, _created = BellTowerLink.objects.update_or_create(
        user=user, belltower_url=cfg['url'], defaults={'username': data['username']},
    )
    return link.username


def queue_link(user):
    """Link a newly registered user in the background."""
    if user.email and is_connected():
        run_after_commit(_link_user_id, user.pk)


def _link_user_id(user_pk):
    from django.contrib.auth.models import User

    user = User.objects.filter(pk=user_pk).first()
    if user:
        linked_username(user)


def link_all_users():
    """Link every active user with an email (run in the background after connecting)."""
    from django.contrib.auth.models import User

    for user in User.objects.filter(is_active=True).exclude(email='').order_by('pk'):
        try:
            linked_username(user)
        except BellTowerError as exc:
            if exc.status == 403:
                raise  # not a staff key: every other user would fail the same way
            print(f'[belltower] Could not link {user.username}: {exc}')


def login_link(username, next_path='/'):
    """A one-time URL that signs ``username`` into Bell Tower and opens ``next_path``."""
    return api('POST', f'users/{urllib.parse.quote(username)}/login-link/', {'next': next_path})['url']
