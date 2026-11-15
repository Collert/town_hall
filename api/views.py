"""JSON API for other applications, authenticated with an API key (Console > Organization > API).

Send the key as `Authorization: Bearer <key>` (or `X-TownHall-API-Key: <key>`). Request bodies
may be JSON or form-encoded. Errors come back as {"error": {"code": ..., "message": ...}}.
The `town-hall-connector` Python package wraps these endpoints.
"""
import json
from datetime import timedelta
from functools import wraps

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from jobs.models import Role

from . import shifts
from .models import APIKey
from .shifts import APIError

API_VERSION = 1


def _error(status, code, message):
    return JsonResponse({'error': {'code': code, 'message': message}}, status=status)


def _key_from(request):
    auth = request.headers.get('Authorization', '')
    if auth[:7].lower() == 'bearer ':
        return auth[7:].strip()
    return request.headers.get('X-TownHall-API-Key', '').strip()


def _params(request):
    if request.method == 'GET':
        return request.GET
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body or b'{}')
        except ValueError:
            raise APIError(400, 'invalid_json', 'The request body is not valid JSON.')
        if not isinstance(data, dict):
            raise APIError(400, 'invalid_json', 'The request body must be a JSON object.')
        return data
    return request.POST


def _flag(value, default):
    if value is None or value == '':
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ('0', 'false', 'no', 'off')


def api_view(*methods):
    def decorator(view):
        @csrf_exempt
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                response = _error(405, 'method_not_allowed', f'Use {" or ".join(methods)}.')
                response['Allow'] = ', '.join(methods)
                return response
            api_key = APIKey.authenticate(_key_from(request))
            if not api_key:
                return _error(401, 'invalid_api_key', 'The API key is missing, revoked or expired.')
            now = timezone.now()
            if not api_key.last_used_at or now - api_key.last_used_at > timedelta(minutes=1):
                APIKey.objects.filter(pk=api_key.pk).update(last_used_at=now)
            try:
                return view(request, api_key, _params(request), *args, **kwargs)
            except APIError as exc:
                return _error(exc.status, exc.code, exc.message)
        return wrapper
    return decorator


def _person_and_role(api_key, params):
    for name in ('town_hall_id', 'role_id'):
        if not str(params.get(name) or '').strip():
            raise APIError(400, 'missing_parameter', f'{name} is required.')
    return shifts.find_person(params['town_hall_id']), shifts.find_role(api_key, params['role_id'])


@api_view('GET')
def index(request, api_key, params):
    """Which key this is, the roles it may use and the heartbeat timing to follow."""
    roles = api_key.roles.all() if api_key.roles.exists() else Role.objects.filter(permanent=True)
    return JsonResponse({
        'api_version': API_VERSION,
        'key': {'name': api_key.name, 'prefix': api_key.prefix,
                'expires_at': api_key.expires_at.isoformat() if api_key.expires_at else None},
        'roles': [{'id': r.pk, 'name': r.name} for r in roles.filter(permanent=True).order_by('name')],
        'heartbeat_interval_seconds': int(shifts.HEARTBEAT_INTERVAL.total_seconds()),
        'heartbeat_timeout_seconds': int(shifts.HEARTBEAT_TIMEOUT.total_seconds()),
    })


@api_view('POST')
def shift_start(request, api_key, params):
    user, role = _person_and_role(api_key, params)
    shift, started = shifts.start(api_key, user, role)
    return JsonResponse({'started': started, 'shift': shifts.shift_data(shift, user)}, status=201 if started else 200)


@api_view('POST')
def shift_heartbeat(request, api_key, params):
    user, role = _person_and_role(api_key, params)
    shift, started = shifts.heartbeat(api_key, user, role, start_if_needed=_flag(params.get('start'), True))
    return JsonResponse({'started': started, 'shift': shifts.shift_data(shift, user)})


@api_view('POST')
def shift_end(request, api_key, params):
    user, role = _person_and_role(api_key, params)
    shift = shifts.end(user, role)
    return JsonResponse({'ended': shift is not None, 'shift': shifts.shift_data(shift, user)})


@api_view('GET')
def shift_status(request, api_key, params):
    user, role = _person_and_role(api_key, params)
    return JsonResponse({'shift': shifts.shift_data(shifts.status(user, role), user)})
