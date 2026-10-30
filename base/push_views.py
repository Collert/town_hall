"""The PWA's service worker and offline page, device registration for web push, and the
listmonk messenger endpoint (base/push.py). All routed outside the language prefix."""
import base64
import json
import secrets

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import listmonk, push
from .models import PushSubscription


def service_worker(request):
    """Served from the site root so it controls every page."""
    response = render(request, 'base/sw.js', {
        'public_key': push.public_key(),
        'offline_url': reverse('offline'),
        'renew_url': reverse('push_renew'),
    }, content_type='application/javascript')
    response['Cache-Control'] = 'no-cache'
    return response


def offline(request):
    """Shown by the service worker when a page can't load without a connection."""
    return render(request, 'base/offline.html')


def _subscription(data):
    """(endpoint, p256dh, auth) from a PushSubscription's toJSON(), or None."""
    endpoint = data.get('endpoint') if isinstance(data, dict) else None
    keys = (data.get('keys') or {}) if endpoint else {}
    if not (isinstance(endpoint, str) and len(endpoint) <= 1000 and push.is_push_service(endpoint)
            and isinstance(keys.get('p256dh'), str) and isinstance(keys.get('auth'), str)):
        return None
    return endpoint, keys['p256dh'][:200], keys['auth'][:100]


def _json(request):
    try:
        return json.loads(request.body or b'{}')
    except ValueError:
        return {}


@login_required
@require_POST
def subscribe(request):
    """Register this browser for the signed-in user. A device that was someone else's
    (another account signed in on it) moves to this one."""
    parsed = _subscription(_json(request))
    if not parsed:
        return JsonResponse({'error': 'invalid subscription'}, status=400)
    endpoint, p256dh, auth = parsed
    previous = PushSubscription.objects.filter(endpoint=endpoint).select_related('user').first()
    PushSubscription.objects.update_or_create(endpoint=endpoint, defaults={
        'user': request.user, 'p256dh': p256dh, 'auth': auth,
        'user_agent': request.headers.get('User-Agent', '')[:300],
    })
    request.session['push_endpoint'] = endpoint
    if previous and previous.user_id != request.user.pk:
        listmonk.queue_sync(previous.user)
    if not previous or previous.user_id != request.user.pk:
        listmonk.queue_sync(request.user)
    return JsonResponse({'ok': True})


@login_required
@require_POST
def unsubscribe(request):
    endpoint = _json(request).get('endpoint')
    if PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()[0]:
        listmonk.queue_sync(request.user)
    request.session.pop('push_endpoint', None)
    return JsonResponse({'ok': True})


@csrf_exempt
@require_POST
def renew(request):
    """The browser replaced a subscription (``pushsubscriptionchange`` in the service
    worker, which has no session). Knowing the old endpoint proves it's the same device."""
    data = _json(request)
    parsed = _subscription(data.get('subscription'))
    old = PushSubscription.objects.filter(endpoint=data.get('old_endpoint') or '-').first()
    if not parsed or not old:
        return JsonResponse({'error': 'unknown subscription'}, status=404)
    old.endpoint, old.p256dh, old.auth = parsed
    PushSubscription.objects.filter(endpoint=old.endpoint).exclude(pk=old.pk).delete()
    old.save()
    listmonk.queue_sync(old.user)
    return JsonResponse({'ok': True})


def _messenger_auth_ok(request):
    scheme, _sep, value = request.headers.get('Authorization', '').partition(' ')
    if scheme.lower() != 'basic':
        return False
    try:
        username, _sep, password = base64.b64decode(value).decode().partition(':')
    except (ValueError, UnicodeDecodeError):
        return False
    return (secrets.compare_digest(username, push.MESSENGER_USER)
            and secrets.compare_digest(password, push.messenger_password()))


@csrf_exempt
@require_POST
def listmonk_messenger(request):
    """listmonk posts each campaign message here when the campaign uses the "townhall"
    messenger (https://listmonk.app/docs/messengers/), signed in with BasicAuth."""
    if not _messenger_auth_ok(request):
        return HttpResponse(status=401, headers={'WWW-Authenticate': 'Basic realm="Town Hall messenger"'})
    data = _json(request)
    if not isinstance(data, dict):
        return JsonResponse({'error': 'invalid message'}, status=400)
    sent = push.deliver_campaign(data, request.build_absolute_uri('/'))
    return JsonResponse({'sent': sent})
