import json

from django.contrib.messages import get_messages


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
