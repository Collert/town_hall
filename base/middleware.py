import json

from django.conf import settings
from django.contrib.messages import get_messages


class SavedLanguageMiddleware:
    """Use a signed-in user's saved language when the browser has no language cookie.

    Without it, Django falls back to the browser's Accept-Language, so someone on a new
    device would lose their saved choice. An explicit choice (the cookie) still wins, and
    visitors with no saved language keep getting the browser language. Runs before
    LocaleMiddleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if settings.LANGUAGE_COOKIE_NAME not in request.COOKIES and user is not None and user.is_authenticated:
            from .models import Profile

            lang = Profile.objects.filter(user=user).values_list('language', flat=True).first()
            if lang and lang in dict(settings.LANGUAGES):
                request.COOKIES[settings.LANGUAGE_COOKIE_NAME] = lang
        return self.get_response(request)


class RememberLanguageMiddleware:
    """Keep ``Profile.language`` set to the language each signed-in user browses in, so
    emails about them (base/email.py) use the listmonk template in that language.

    The session remembers what was saved, so this only writes when the language changes.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        lang = getattr(request, 'LANGUAGE_CODE', None)
        user = getattr(request, 'user', None)
        # The kiosk runs on a staff member's session; a volunteer switching its language
        # shouldn't change which language that staff member's emails arrive in.
        on_kiosk = request.resolver_match is not None and (request.resolver_match.url_name or '').startswith('kiosk')
        if (lang and user is not None and user.is_authenticated and not on_kiosk
                and request.session.get('profile_language') != lang):
            from .models import Profile

            Profile.objects.filter(user=user).update(language=lang)
            request.session['profile_language'] = lang
        return response


class HtmxMessagesMiddleware:
    """Deliver Django messages queued during an HTMX request as toasts.

    HTMX swaps only a fragment, so the toast container in the layout never
    re-renders. Instead, pending messages are drained into an ``HX-Trigger``
    header that ``base/toasts.js`` listens for.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        is_htmx = request.headers.get('HX-Request') == 'true'
        # A redirect (or HX-Redirect) renders a full page that shows messages itself.
        if not is_htmx or 300 <= response.status_code < 400 or 'HX-Redirect' in response:
            return response

        items = [{'message': str(m), 'tags': m.tags} for m in get_messages(request)]
        if not items:
            return response

        triggers = {}
        if 'HX-Trigger' in response:
            try:
                triggers = json.loads(response['HX-Trigger'])
            except ValueError:
                triggers = {response['HX-Trigger']: None}
        triggers['toasts'] = {'items': items}
        response['HX-Trigger'] = json.dumps(triggers)
        return response


class FirstRunMiddleware:
    """Send every request to the setup page while the database has no users, so the first person
    to open a fresh install creates the superuser (base.views.first_run_setup).

    Once a user exists it never checks again in this process.
    """

    has_users = False

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if FirstRunMiddleware.has_users:
            return None
        from django.contrib.auth.models import User

        if User.objects.exists():
            FirstRunMiddleware.has_users = True
            return None
        # The setup page itself, media, icons, the language switcher and the machine-facing MCP/OAuth endpoints.
        name = request.resolver_match.url_name if request.resolver_match else None
        if name in ('first_run_setup', 'set_language', 'favicon', 'web_manifest', 'service_worker', 'offline') or view_func.__module__.startswith(('django.views.static', 'mcp_server')):
            return None
        from django.shortcuts import redirect

        return redirect('first_run_setup')


class ProfileReminderMiddleware:
    """Sends due complete-your-profile reminder emails, checking at most every few minutes
    as requests come in, so it works without a scheduler (base/profile_completion.py)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .profile_completion import run_due_if_idle
        run_due_if_idle(request)
        return self.get_response(request)
