"""OAuth 2.1 for the MCP connector, so Claude (web, desktop, mobile) can connect as a
signed-in staff member.

The flow an MCP client follows:
1. POST /mcp without a token gets 401 with a WWW-Authenticate header pointing at the
   protected resource metadata (RFC 9728), which names this site as the authorization
   server; its metadata (RFC 8414) lists the endpoints below.
2. The client registers itself at /oauth/register (RFC 7591).
3. The person is sent to /oauth/authorize, signs in to Town Hall if needed, and approves.
   Only active staff can approve.
4. The client trades the code at /oauth/token, proving it started the flow (PKCE, S256),
   and gets an access token (1 hour) plus a refresh token (60 days, rotated on use).

These URLs sit outside the language prefix: clients build them from the metadata.
"""
import base64
import hashlib
import json
from datetime import timedelta
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from django.contrib.auth.views import redirect_to_login
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import AccessToken, AuthorizationCode, OAuthClient, digest, new_secret

SCOPE = 'townhall'
CODE_TTL = timedelta(minutes=5)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=60)
CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Authorization, Content-Type, Mcp-Protocol-Version, Mcp-Session-Id',
    'Access-Control-Expose-Headers': 'WWW-Authenticate, Mcp-Session-Id',
}


def base_url(request):
    return f'{request.scheme}://{request.get_host()}'


def resource_url(request):
    return base_url(request) + '/mcp'


def with_cors(response):
    for name, value in CORS_HEADERS.items():
        response[name] = value
    return response


def preflight():
    return with_cors(HttpResponse(status=204))


def _json(data, status=200):
    response = JsonResponse(data, status=status)
    response['Cache-Control'] = 'no-store'
    return with_cors(response)


def _error(code, description, status=400):
    return _json({'error': code, 'error_description': description}, status)


def can_connect(user):
    return user.is_authenticated and user.is_active and user.is_staff


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def protected_resource_metadata(request):
    if request.method == 'OPTIONS':
        return preflight()
    return _json({
        'resource': resource_url(request),
        'authorization_servers': [base_url(request)],
        'scopes_supported': [SCOPE],
        'bearer_methods_supported': ['header'],
        'resource_name': 'Town Hall',
    })


def authorization_server_metadata(request):
    if request.method == 'OPTIONS':
        return preflight()
    base = base_url(request)
    return _json({
        'issuer': base,
        'authorization_endpoint': base + '/oauth/authorize',
        'token_endpoint': base + '/oauth/token',
        'registration_endpoint': base + '/oauth/register',
        'revocation_endpoint': base + '/oauth/revoke',
        'response_types_supported': ['code'],
        'grant_types_supported': ['authorization_code', 'refresh_token'],
        'code_challenge_methods_supported': ['S256'],
        'token_endpoint_auth_methods_supported': ['none', 'client_secret_post', 'client_secret_basic'],
        'scopes_supported': [SCOPE],
    })


def unauthorized(request):
    """401 for /mcp, telling the client where to start signing in."""
    response = _json({'error': 'invalid_token', 'error_description': 'Connect this app to Town Hall first.'}, 401)
    response['WWW-Authenticate'] = (
        f'Bearer resource_metadata="{base_url(request)}/.well-known/oauth-protected-resource/mcp"'
    )
    return response


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def _valid_redirect(uri):
    parts = urlsplit(uri or '')
    if parts.fragment or not parts.netloc:
        return False
    if parts.scheme == 'https':
        return True
    # Desktop apps and local tools listen on the loopback interface.
    return parts.scheme == 'http' and parts.hostname in ('localhost', '127.0.0.1', '::1')


@csrf_exempt
def register(request):
    if request.method == 'OPTIONS':
        return preflight()
    if request.method != 'POST':
        return _error('invalid_request', 'Use POST.', 405)
    try:
        data = json.loads(request.body or b'{}')
    except ValueError:
        return _error('invalid_client_metadata', 'The body must be JSON.')
    uris = data.get('redirect_uris')
    if not isinstance(uris, list) or not uris or not all(isinstance(u, str) and _valid_redirect(u) for u in uris):
        return _error('invalid_redirect_uri', 'redirect_uris must be https (or http on localhost) URLs.')
    method = data.get('token_endpoint_auth_method') or 'none'
    if method not in ('none', 'client_secret_post', 'client_secret_basic'):
        return _error('invalid_client_metadata', f'Unsupported token_endpoint_auth_method {method!r}.')

    client = OAuthClient(client_id=new_secret('thc')[:48], name=str(data.get('client_name') or '')[:200], redirect_uris=uris)
    secret = None
    if method != 'none':
        secret = new_secret('ths')
        client.secret_hash = digest(secret)
    client.save()
    body = {
        'client_id': client.client_id,
        'client_id_issued_at': int(client.created_at.timestamp()),
        'client_name': client.name,
        'redirect_uris': uris,
        'grant_types': ['authorization_code', 'refresh_token'],
        'response_types': ['code'],
        'token_endpoint_auth_method': method,
        'scope': SCOPE,
    }
    if secret:
        body.update(client_secret=secret, client_secret_expires_at=0)
    return _json(body, 201)


# ---------------------------------------------------------------------------
# Authorization (the person approves)
# ---------------------------------------------------------------------------

def _redirect_with(uri, **params):
    parts = urlsplit(uri)
    query = parse_qsl(parts.query, keep_blank_values=True) + [(k, v) for k, v in params.items() if v]
    return HttpResponseRedirect(urlunsplit(parts._replace(query=urlencode(query))))


def authorize(request):
    params = request.POST if request.method == 'POST' else request.GET
    client = OAuthClient.objects.filter(client_id=params.get('client_id', '')).first()
    redirect_uri = params.get('redirect_uri', '')
    if not client or redirect_uri not in client.redirect_uris:
        # Never redirect to an address we can't vouch for.
        return render(request, 'mcp_server/authorize_error.html', {
            'message': 'This connection link is invalid or out of date. Start connecting again from the app.',
        }, status=400)
    state = params.get('state', '')
    if params.get('response_type') != 'code':
        return _redirect_with(redirect_uri, error='unsupported_response_type', state=state)
    challenge = params.get('code_challenge', '')
    if not challenge or params.get('code_challenge_method') != 'S256':
        return _redirect_with(redirect_uri, error='invalid_request',
                              error_description='PKCE with S256 is required.', state=state)

    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    if not can_connect(request.user):
        return render(request, 'mcp_server/authorize_error.html', {
            'message': 'Only staff accounts can connect AI apps to Town Hall.',
        }, status=403)

    if request.method == 'POST':
        if request.POST.get('decision') != 'allow':
            return _redirect_with(redirect_uri, error='access_denied', state=state)
        code = new_secret('thk')
        AuthorizationCode.objects.create(
            code_hash=digest(code), client=client, user=request.user, redirect_uri=redirect_uri,
            code_challenge=challenge, expires_at=timezone.now() + CODE_TTL,
        )
        return _redirect_with(redirect_uri, code=code, state=state)

    return render(request, 'mcp_server/authorize.html', {
        'client': client,
        'client_name': client.name or urlsplit(redirect_uri).hostname,
        'redirect_host': urlsplit(redirect_uri).hostname,
        'fields': {k: params.get(k, '') for k in (
            'client_id', 'redirect_uri', 'response_type', 'code_challenge', 'code_challenge_method', 'state', 'scope', 'resource',
        )},
    })


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def _client_from_request(request):
    """(client, error response). Public clients send only client_id; confidential ones
    authenticate with their secret in the body or a Basic header."""
    client_id, secret = request.POST.get('client_id', ''), request.POST.get('client_secret', '')
    header = request.headers.get('Authorization', '')
    if header.startswith('Basic '):
        try:
            client_id, _sep, secret = base64.b64decode(header[6:]).decode().partition(':')
        except ValueError:
            return None, _error('invalid_client', 'Malformed Basic credentials.', 401)
    client = OAuthClient.objects.filter(client_id=client_id).first()
    if not client:
        return None, _error('invalid_client', 'Unknown client.', 401)
    if client.secret_hash and digest(secret) != client.secret_hash:
        return None, _error('invalid_client', 'Wrong client secret.', 401)
    return client, None


def _pkce_ok(verifier, challenge):
    if not verifier:
        return False
    computed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return computed == challenge


def _issue(client, user, token=None):
    access, refresh = new_secret('tha'), new_secret('thr')
    now = timezone.now()
    token = token or AccessToken(client=client, user=user)
    token.access_hash, token.refresh_hash = digest(access), digest(refresh)
    token.expires_at, token.refresh_expires_at = now + ACCESS_TTL, now + REFRESH_TTL
    token.save()
    return _json({
        'access_token': access, 'token_type': 'Bearer', 'expires_in': int(ACCESS_TTL.total_seconds()),
        'refresh_token': refresh, 'scope': SCOPE,
    })


@csrf_exempt
def token(request):
    if request.method == 'OPTIONS':
        return preflight()
    if request.method != 'POST':
        return _error('invalid_request', 'Use POST.', 405)
    client, error = _client_from_request(request)
    if error:
        return error
    grant = request.POST.get('grant_type')

    if grant == 'authorization_code':
        with transaction.atomic():
            code = (AuthorizationCode.objects.select_for_update().select_related('user')
                    .filter(code_hash=digest(request.POST.get('code', '')), client=client).first())
            if not code or code.used or code.expires_at < timezone.now():
                return _error('invalid_grant', 'The code is invalid, used or expired.')
            code.used = True
            code.save(update_fields=['used'])
        if request.POST.get('redirect_uri', code.redirect_uri) != code.redirect_uri:
            return _error('invalid_grant', 'redirect_uri does not match.')
        if not _pkce_ok(request.POST.get('code_verifier', ''), code.code_challenge):
            return _error('invalid_grant', 'PKCE verification failed.')
        if not can_connect(code.user):
            return _error('invalid_grant', 'This account can no longer connect.')
        return _issue(client, code.user)

    if grant == 'refresh_token':
        with transaction.atomic():
            row = (AccessToken.objects.select_for_update().select_related('user')
                   .filter(refresh_hash=digest(request.POST.get('refresh_token', '')), client=client).first())
            if not row or not row.active:
                return _error('invalid_grant', 'The refresh token is invalid or expired.')
            if not can_connect(row.user):
                return _error('invalid_grant', 'This account can no longer connect.')
            return _issue(client, row.user, token=row)  # rotates both tokens

    return _error('unsupported_grant_type', 'Use authorization_code or refresh_token.')


@csrf_exempt
def revoke(request):
    if request.method == 'OPTIONS':
        return preflight()
    value = digest(request.POST.get('token', ''))
    AccessToken.objects.filter(revoked=False).filter(access_hash=value).update(revoked=True)
    AccessToken.objects.filter(revoked=False).filter(refresh_hash=value).update(revoked=True)
    return with_cors(HttpResponse(status=200))


def authenticate(request):
    """The staff member a Bearer token acts for, or None."""
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        return None
    row = (AccessToken.objects.select_related('user')
           .filter(access_hash=digest(header[7:].strip()), revoked=False, expires_at__gt=timezone.now()).first())
    if not row or not can_connect(row.user):
        return None
    now = timezone.now()
    if not row.last_used_at or now - row.last_used_at > timedelta(minutes=1):
        AccessToken.objects.filter(pk=row.pk).update(last_used_at=now)
    return row.user
