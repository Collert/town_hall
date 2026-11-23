"""Town Hall's Switchboard endpoints (Switchboard's docs/PROTOCOL.md, version 1).

- ``GET  /.well-known/switchboard``   the manifest: what Town Hall emits and can do
- ``GET  /switchboard/authorize``     consent page; staff approve, Switchboard gets a one-time code
- ``POST /switchboard/v1/token``      Switchboard trades the code for its API key and webhook secret
- ``GET  /switchboard/v1/``           checks a key
- ``POST /switchboard/v1/actions/<name>``  runs an action (switchboard/actions.py)
- ``GET  /switchboard/v1/options/<name>``  a list a param offers as a dropdown (switchboard/options.py)
- ``DELETE /switchboard/v1/connection``    Switchboard disconnecting

These paths sit outside the language prefix: Switchboard builds them from the manifest.
Errors are {"error": {"code", "message"}}.
"""
import json
import logging
import secrets
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt

from . import actions, manifest, options
from .models import AuthorizeCode, IdempotencyRecord, SwitchboardConnection, digest

logger = logging.getLogger(__name__)

CODE_TTL = timedelta(minutes=10)
IDEMPOTENCY_KEY_MAX = 200


def _json(data, status=200):
    response = JsonResponse(data, status=status)
    response['Cache-Control'] = 'no-store'
    return response


def _error(status, code, message):
    return _json({'error': {'code': code, 'message': message}}, status)


def _method_not_allowed(*methods):
    response = _error(405, 'method_not_allowed', f'Use {" or ".join(methods)}.')
    response['Allow'] = ', '.join(methods)
    return response


def _body(request):
    """The request's JSON object, or None when it isn't one. An empty body is an empty object."""
    if not request.body.strip():
        return {}
    try:
        data = json.loads(request.body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _web_url(value):
    """True for an absolute http(s) address."""
    if not isinstance(value, str):
        return False
    parts = urlsplit(value.strip())
    return parts.scheme in ('http', 'https') and bool(parts.hostname)


def can_connect(user):
    return user.is_authenticated and user.is_active and user.is_staff


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def manifest_view(request):
    if request.method != 'GET':
        return _method_not_allowed('GET')
    return _json(manifest.build(request))


# ---------------------------------------------------------------------------
# Connecting: consent page, then the token exchange
# ---------------------------------------------------------------------------

def _redirect_with(uri, **params):
    parts = urlsplit(uri)
    query = parse_qsl(parts.query, keep_blank_values=True) + [(k, v) for k, v in params.items() if v]
    return HttpResponseRedirect(urlunsplit(parts._replace(query=urlencode(query))))


def _authorize_error(request, message, status):
    return render(request, 'switchboard/authorize_error.html', {'message': message}, status=status)


def authorize(request):
    params = request.POST if request.method == 'POST' else request.GET
    callback = params.get('callback', '').strip()
    if not _web_url(callback) or urlsplit(callback).fragment:
        # Never send anyone to an address that isn't a plain web address.
        return _authorize_error(request, _('This connection link is invalid or out of date. Start connecting again from Switchboard.'), 400)
    state = params.get('state', '')
    host = urlsplit(callback).netloc
    name = ' '.join(params.get('name', '').split())[:200]

    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    if not can_connect(request.user):
        return _authorize_error(request, _('Only staff accounts can connect Switchboard to Town Hall.'), 403)

    if request.method == 'POST':
        if request.POST.get('decision') != 'allow':
            return _redirect_with(callback, error='access_denied', state=state)
        code = 'sbc_' + secrets.token_urlsafe(32)
        AuthorizeCode.objects.create(code_hash=digest(code), user=request.user, name=name, callback=callback[:500],
                                     expires_at=timezone.now() + CODE_TTL)
        return _redirect_with(callback, code=code, state=state)

    return render(request, 'switchboard/authorize.html', {
        'switchboard_name': name,
        'callback_host': host,
        'fields': {'callback': callback, 'state': state, 'name': name},
    })


@csrf_exempt
def token(request):
    if request.method != 'POST':
        return _method_not_allowed('POST')
    data = _body(request)
    if data is None:
        return _error(400, 'invalid_request', 'The request body must be a JSON object.')
    webhook_url = data.get('webhook_url')
    if not _web_url(webhook_url) or len(webhook_url) > 500:
        return _error(400, 'invalid_request', 'webhook_url must be an http(s) address.')
    code = data.get('code')
    now = timezone.now()
    with transaction.atomic():
        row = (AuthorizeCode.objects.select_for_update()
               .filter(code_hash=digest(code if isinstance(code, str) else '')).select_related('user').first())
        if not row or row.used_at or row.expires_at <= now or not can_connect(row.user):
            return _error(400, 'invalid_code', 'This code is wrong, was already used or has expired. Start connecting again.')
        row.used_at = now
        row.save(update_fields=['used_at'])
        name = data.get('name') if isinstance(data.get('name'), str) else ''
        # Connecting again from the same Switchboard replaces its earlier connection.
        SwitchboardConnection.objects.filter(webhook_url=webhook_url.strip()).delete()
        connection, api_key = SwitchboardConnection.create(
            webhook_url.strip(), name=' '.join(name.split()) or row.name,
            base_url=manifest.base_url(request), created_by=row.user)
    return _json({
        'api_key': api_key,
        'webhook_secret': connection.webhook_secret,
        'connection_id': str(connection.pk),
        'building': manifest.BUILDING,
        'instance': manifest.instance_name(),
    })


# ---------------------------------------------------------------------------
# The API Switchboard calls with its key
# ---------------------------------------------------------------------------

def api_view(*methods):
    def decorator(view):
        @csrf_exempt
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                return _method_not_allowed(*methods)
            auth = request.headers.get('Authorization', '')
            connection = SwitchboardConnection.authenticate(auth[7:]) if auth[:7].lower() == 'bearer ' else None
            if not connection:
                return _error(401, 'unauthorized', 'The key is missing or wrong, or this connection was disconnected in Town Hall.')
            now = timezone.now()
            if not connection.last_used_at or now - connection.last_used_at > timedelta(minutes=1):
                SwitchboardConnection.objects.filter(pk=connection.pk).update(last_used_at=now)
            try:
                return view(request, connection, *args, **kwargs)
            except Exception:
                logger.exception('Switchboard: %s %s failed', request.method, request.path)
                return _error(500, 'server_error', 'Something went wrong in Town Hall. Try again in a moment.')
        return wrapper
    return decorator


@api_view('GET')
def index(request, connection):
    return _json({'ok': True, 'building': manifest.BUILDING, 'instance': manifest.instance_name(),
                  'connection_id': str(connection.pk)})


@api_view('GET')
def options_view(request, connection, name):
    source = options.SOURCES.get(name)
    if not source:
        return _error(404, 'unknown_options', f'Town Hall has no list of options called "{name}".')
    # Names are offered in the default language: the one actions match them in.
    with translation.override(settings.LANGUAGE_CODE):
        return _json({'options': source()[:options.OPTIONS_LIMIT]})


@api_view('DELETE')
def connection_view(request, connection):
    connection.delete()
    return _json({'ok': True})


def _number(value, whole):
    """`value` as an int or float, or None when it isn't a number. Text such as "3" counts:
    a workflow may pass along a number another app sent as text."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if not isinstance(value, (int, float)) or value != value or value in (float('inf'), float('-inf')):
        return None
    if whole:
        return int(value) if float(value).is_integer() else None
    return value


def _datetime(value):
    """An ISO 8601 time as an aware datetime (without an offset it is the organization's local time), or None."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


def _check_params(spec, body):
    """The action's params from the request body, each as its manifest type and within its limits
    (max_length, min, max, choices): text as str, `int` and `number` as numbers, `bool` as a bool
    and `datetime` as an aware datetime. IDs may arrive as numbers; they are read as text.
    Missing optional params are left out. Raises ActionError (422) naming the param."""
    params = {}
    for item in spec['params']:
        key, type_, value = item['key'], item['type'], body.get(item['key'])
        if isinstance(value, (int, float)) and not isinstance(value, bool) and type_ == 'string':
            value = str(int(value)) if float(value).is_integer() else str(value)
        if value is None or (isinstance(value, str) and not value.strip()):
            if item['required']:
                raise actions.invalid(f'{key} is required.')
            continue
        if type_ == 'string':
            if not isinstance(value, str):
                raise actions.invalid(f'{key} must be text.')
            if 'max_length' in item and len(value.strip()) > item['max_length']:
                raise actions.invalid(f'{key} is too long ({item["max_length"]} characters at most).')
        elif type_ in ('int', 'number', 'money'):
            value = _number(value, whole=type_ == 'int')
            if value is None:
                raise actions.invalid(f'{key} must be a {"whole number" if type_ == "int" else "number"}.')
            if 'min' in item and value < item['min']:
                raise actions.invalid(f'{key} must be at least {item["min"]}.')
            if 'max' in item and value > item['max']:
                raise actions.invalid(f'{key} must be at most {item["max"]}.')
        elif type_ == 'bool':
            if isinstance(value, str) and value.strip().lower() in ('true', 'false', 'yes', 'no'):
                value = value.strip().lower() in ('true', 'yes')
            if not isinstance(value, bool):
                raise actions.invalid(f'{key} must be true or false.')
        elif type_ == 'datetime':
            value = _datetime(value)
            if value is None:
                raise actions.invalid(f'{key} must be a date and time like 2026-12-06T09:00:00-08:00.')
        if 'choices' in item and value not in [choice['value'] for choice in item['choices']]:
            raise actions.invalid(f'{key} must be one of: ' + ', '.join(str(choice['value']) for choice in item['choices']) + '.')
        params[key] = value
    return params


def _run_action(request, connection, name, spec, body):
    """(status, body) for one action call. The work runs in a transaction, so an error leaves
    nothing half-done, and in the organization's default language: names are matched and
    written in it, whatever language Switchboard's request asked for."""
    try:
        with transaction.atomic(), translation.override(settings.LANGUAGE_CODE):
            return 200, actions.HANDLERS[name](connection, _check_params(spec, body), request)
    except actions.ActionError as exc:
        return exc.status, {'error': {'code': exc.code, 'message': exc.message}}


@api_view('POST')
def action(request, connection, name):
    spec = manifest.ACTIONS_BY_NAME.get(name)
    if not spec:
        return _error(404, 'unknown_action', f'Town Hall has no action called "{name}".')
    body = _body(request)
    if body is None:
        return _error(400, 'invalid_request', 'The request body must be a JSON object.')

    key = request.headers.get('Idempotency-Key', '').strip()
    if spec['kind'] != 'action' or not key:
        status, result = _run_action(request, connection, name, spec, body)
        return _json(result, status)
    if len(key) > IDEMPOTENCY_KEY_MAX:
        return _error(422, 'invalid_params', f'Idempotency-Key is too long ({IDEMPOTENCY_KEY_MAX} characters at most).')

    # A repeated key gets the answer the first call got, and the work isn't done again.
    def stored():
        record = IdempotencyRecord.objects.filter(connection=connection, key=key).first()
        return _json(record.response, record.status) if record else None

    replay = stored()
    if replay:
        return replay
    try:
        # The answer is saved with the work: both happen, or neither.
        with transaction.atomic():
            status, result = _run_action(request, connection, name, spec, body)
            IdempotencyRecord.objects.create(connection=connection, key=key, action=name, status=status, response=result)
    except IntegrityError:
        # The same key arrived twice at once: this call's work is undone and the other's answer stands.
        replay = stored()
        if replay:
            return replay
        raise
    return _json(result, status)
