import math
import os
import secrets

from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.utils import timezone, translation
from django.utils.translation import gettext as _, gettext_lazy

from base import ai, belltower, listmonk
from django.db import transaction

from base import points
from django.contrib.auth.models import User

from base.models import BellTowerLink, HeroSection, Level, PointsRules, Profile, SiteSettings
from town_hall import site_config

from ..decorators import staff_required
from .. import auto_translate
from ..forms import BackendSettingsForm, HeroSectionForm, LevelFormSet, OrganizationForm, PointsRulesForm, RegionSettingsForm

# (field, CSS variable it previews live, label) grouped for the template.
# Dark-mode fields have no live preview variable.
COLOR_GROUPS = [
    (gettext_lazy('Background Colors'), [
        ('color_bg_primary', '--color-bg-primary', gettext_lazy('Primary')),
        ('color_bg_secondary', '--color-bg-secondary', gettext_lazy('Secondary')),
        ('color_bg_tertiary', '--color-bg-tertiary', gettext_lazy('Tertiary')),
    ], [
        ('dark_bg_primary', '', gettext_lazy('Primary')),
        ('dark_bg_secondary', '', gettext_lazy('Secondary')),
        ('dark_bg_tertiary', '', gettext_lazy('Tertiary')),
    ]),
    (gettext_lazy('Text Colors'), [
        ('color_text_primary', '--color-text-primary', gettext_lazy('Primary')),
        ('color_text_secondary', '--color-text-secondary', gettext_lazy('Secondary')),
        ('color_text_tertiary', '--color-text-tertiary', gettext_lazy('Tertiary')),
    ], [
        ('dark_text_primary', '', gettext_lazy('Primary')),
        ('dark_text_secondary', '', gettext_lazy('Secondary')),
        ('dark_text_tertiary', '', gettext_lazy('Tertiary')),
    ]),
    (gettext_lazy('UI Elements'), [
        ('color_border', '--color-border', gettext_lazy('Border')),
        ('color_divider', '--color-divider', gettext_lazy('Divider')),
    ], [
        ('dark_border', '', gettext_lazy('Border')),
        ('dark_divider', '', gettext_lazy('Divider')),
    ]),
]

STATUS_COLORS = [
    ('color_success', '--color-success', gettext_lazy('Success')),
    ('color_warning', '--color-warning', gettext_lazy('Warning')),
    ('color_error', '--color-error', gettext_lazy('Error')),
]


@staff_required
def organization_settings(request):
    site = SiteSettings.get_settings()
    if request.method == 'POST':
        form = OrganizationForm(request.POST, request.FILES, instance=site)
        if form.is_valid():
            form.save()
            messages.success(request, _('Organization settings saved.'))
            branding = {'company_name', 'logo'} | {name for name in form.changed_data if name.startswith('color_')}
            if listmonk.is_configured() and branding & set(form.changed_data):
                listmonk.run_in_background(_('Updating email branding'), listmonk.refresh_brand)
                messages.info(request, _('Your listmonk email templates are being updated with the new branding.'))
            return redirect('console_settings')
        messages.error(request, _('Please fix the highlighted fields.'))
    else:
        form = OrganizationForm(instance=site)

    def bind(rows):
        return [(form[name], var, label) for name, var, label in rows]

    filled = [site.company_name, site.logo, site.contact_email, site.terms_of_service, site.careers_page_url]
    return render(request, 'console/organization_settings.html', {
        'form': form,
        'site': site,
        'color_groups': [(title, bind(light), bind(dark)) for title, light, dark in COLOR_GROUPS],
        'status_colors': bind(STATUS_COLORS),
        'setup_percent': round(sum(1 for f in filled if f) / len(filled) * 100),
    })


@staff_required
def home_page_settings(request):
    hero, _created = HeroSection.objects.get_or_create(pk=1)
    if request.method == 'POST':
        form = HeroSectionForm(request.POST, request.FILES, instance=hero)
        if form.is_valid():
            form.save()
            messages.success(request, _('Home page banner saved.'))
            return redirect('console_settings_home')
        messages.error(request, _('Please fix the highlighted fields.'))
    else:
        form = HeroSectionForm(instance=hero)
    return render(request, 'console/home_page_settings.html', {'form': form, 'hero': hero})


@staff_required
def level_settings(request):
    levels = Level.objects.order_by('min_points')
    if request.method == 'POST':
        formset = LevelFormSet(request.POST, queryset=levels)
        if formset.is_valid():
            with transaction.atomic():
                for level in formset.save(commit=False):
                    if level.numeric_name is None:
                        level.numeric_name = 200000 + level.min_points  # placeholder, renumbered below
                    level.save()
                for level in formset.deleted_objects:
                    level.delete()
                # Level numbers follow the points order. Park them first so the unique column never collides.
                ordered = list(Level.objects.order_by('min_points'))
                for offset, level in enumerate(ordered):
                    Level.objects.filter(pk=level.pk).update(numeric_name=100000 + offset)
                for number, level in enumerate(ordered, start=1):
                    Level.objects.filter(pk=level.pk).update(numeric_name=number)
            messages.success(request, _('Levels saved.'))
            return redirect('console_settings_levels')
        messages.error(request, _('Please fix the highlighted fields.'))
    else:
        formset = LevelFormSet(queryset=levels)

    # How many volunteers each level currently holds
    points = list(Profile.objects.filter(user__is_active=True).values_list('impact_points', flat=True))
    bands = list(levels)
    distribution = []
    for i, level in enumerate(bands):
        upper = bands[i + 1].min_points if i + 1 < len(bands) else None
        count = sum(1 for p in points if p >= level.min_points and (upper is None or p < upper))
        distribution.append({'level': level, 'upper': upper, 'count': count})
    below = sum(1 for p in points if not bands or p < bands[0].min_points)
    return render(request, 'console/level_settings.html', {
        'formset': formset,
        'distribution': distribution,
        'unranked': below,
        'volunteer_total': len(points),
    })


@staff_required
def points_settings(request):
    rules = PointsRules.get()
    if request.method == 'POST' and request.POST.get('action') == 'recalculate':
        points.rebuild()
        messages.success(request, _('Every past shift was recalculated with the current rules.'))
        return redirect('console_settings_points')
    if request.method == 'POST':
        form = PointsRulesForm(request.POST, instance=rules)
        if form.is_valid():
            form.save()
            messages.success(request, _('Points rules saved. They apply to new points from now on.'))
            return redirect('console_settings_points')
        messages.error(request, _('Please fix the highlighted fields.'))
    else:
        form = PointsRulesForm(instance=rules)

    # Worked example with the saved rules: 3 hours, a mid-level role, 25 people per volunteer, early start
    weight = 1 + 2 / 4 * (float(rules.max_role_weight) - 1)
    reach = min(rules.reach_cap_percent, round(points.REACH_PERCENT_PER_DOUBLING * math.log2(1 + 25)))
    example = {
        'hours': 3, 'weight': round(weight, 2), 'reach': reach, 'clutch': rules.off_hours_bonus_percent,
        'total': round(rules.points_per_hour * 3 * weight * (1 + reach / 100) * (1 + rules.off_hours_bonus_percent / 100)),
    }
    return render(request, 'console/points_settings.html', {'form': form, 'rules': rules, 'example': example})


@staff_required
def region_settings(request):
    site = SiteSettings.get_settings()
    if request.method == 'POST':
        form = RegionSettingsForm(request.POST, site=site)
        if form.is_valid():
            form.save()
            if not site_config.restart_pending(site):
                messages.success(request, _('Region settings saved.'))
            else:
                messages.warning(request, _('Region settings saved. Restart Town Hall to apply them.'))
            return redirect('console_settings_region')
        messages.error(request, _('Please fix the highlighted fields.'))
    else:
        form = RegionSettingsForm(site=site)

    running_languages, running_default, running_zone = site_config.running()
    checked = set(form['languages'].value() or [])
    default = form['default_language'].value()
    languages = []
    for code, name in settings.SUPPORTED_LANGUAGES:
        info = translation.get_language_info(code)
        languages.append({
            'code': code, 'name': name, 'name_local': info['name_local'].capitalize(),
            'on': code in checked, 'default': code == default,
            'live': code in dict(running_languages),
        })
    return render(request, 'console/region_settings.html', {
        'form': form,
        'languages': languages,
        'restart_pending': site_config.restart_pending(site),
        'running_languages': [name for _code, name in running_languages],
        'running_default': dict(running_languages)[running_default],
        'running_zone': running_zone,
        'local_time': timezone.localtime(),
        'can_restart': bool(site_config.restart_method(request)),
    })


@staff_required
@require_POST
def restart_app(request):
    """HTMX: restart Town Hall, then show a notice that polls until the new process answers."""
    method = site_config.restart_method(request)
    if not method:
        messages.error(request, _("Town Hall can't restart itself on this server. Restart it from your hosting dashboard."))
        return HttpResponse(status=204)
    site_config.restart(method)
    return render(request, 'console/partials/restart_status.html', {'boot_id': site_config.BOOT_ID})


@staff_required
def restart_status(request):
    """HTMX poll: reload the page once a process other than ?boot= answers."""
    if request.GET.get('boot') != site_config.BOOT_ID:
        return HttpResponse(headers={'HX-Refresh': 'true'})
    return HttpResponse(status=204)


def _connect_listmonk(request):
    """After saving a working connection, set listmonk up in the background: the
    "Town Hall users" list, the logo, and the email templates for every event and language."""
    try:
        listmonk.get_lists()
    except listmonk.ListmonkError as exc:
        messages.warning(request, _("listmonk couldn't be reached: %(error)s") % {'error': exc})
        return
    listmonk.run_in_background(_('Setting up listmonk'), listmonk.connect)
    messages.success(request, _(
        'Connected to listmonk. Setting up the "%(name)s" list and your email templates; this takes a minute.'
    ) % {'name': listmonk.USERS_LIST_NAME})


@staff_required
def backend_settings(request):
    site = SiteSettings.get_settings()
    if request.method == 'POST':
        form = BackendSettingsForm(request.POST, instance=site)
        if form.is_valid():
            form.save()
            messages.success(request, _('Backend settings saved.'))
            if listmonk.is_configured():
                _connect_listmonk(request)
            return redirect('console_settings_backend')
        messages.error(request, _('Please fix the highlighted fields.'))
    else:
        form = BackendSettingsForm(instance=site)

    cfg = auto_translate.config()
    not_set, blocked = _('Not set'), _('Blocked')
    # (name, ready, description, label when not ready)
    providers = [
        ('LibreTranslate', bool(cfg['libretranslate_url']), _('Your own server. Free, private, no limits.'), not_set),
        ('Google Cloud Translation', bool(cfg['google_translate_api_key']), _('Paid, reliable.'), not_set),
        ('DeepL', bool(cfg['deepl_api_key']), _('Free tier: 500,000 characters a month.'), not_set),
        (_('Google Translate (free)'), not auto_translate.google_free_blocked(), _('No key. Google may block your network with a CAPTCHA.'), blocked),
        ('MyMemory', True, _('No key. About 5,000 characters a day, 50,000 with a contact email.'), not_set),
    ]
    listmonk_cfg = listmonk.config()
    listmonk_error = None
    belltower_cfg = belltower.config()
    belltower_error = belltower_me = None
    if belltower.is_connected(belltower_cfg):
        try:
            belltower_me = belltower.me()  # proves the key still works, and says if it's staff
        except belltower.BellTowerError as exc:
            belltower_error = str(exc)
    if listmonk.is_configured(listmonk_cfg):
        try:
            listmonk.get_lists()  # cheapest authenticated call: proves the URL and token work
        except listmonk.ListmonkError as exc:
            listmonk_error = str(exc)
    return render(request, 'console/backend_settings.html', {
        'form': form,
        'secrets': form.saved_secrets(),
        'providers': providers,
        'active_provider': next((p[0] for p in providers if p[1]), 'MyMemory'),
        'google_blocked_minutes': auto_translate.google_free_blocked() // 60,
        'listmonk_configured': listmonk.is_configured(listmonk_cfg),
        'listmonk_error': listmonk_error,
        'listmonk_task': listmonk.current_task(),
        'listmonk_task_error': listmonk.last_error(),
        'listmonk_admin_url': listmonk_cfg['url'] + '/admin' if listmonk_cfg['url'] else '',
        'belltower': belltower_cfg,
        'belltower_connected': belltower.is_connected(belltower_cfg),
        'belltower_error': belltower_error,
        'belltower_connected_at': site.belltower_connected_at,
        'belltower_me': belltower_me,
        'belltower_linked': BellTowerLink.objects.filter(belltower_url=belltower_cfg['url']).count() if belltower_me else 0,
        'belltower_linkable': User.objects.filter(is_active=True).exclude(email='').count() if belltower_me else 0,
        'ai_enabled_without_key': site.ai_enabled and not ai.config()['api_key'],
        'planner_hidden': request.user.profile.ai_planner_hidden,
        'env_overrides': {
            name: bool(os.environ.get(env)) for name, env in (
                ('libretranslate_url', 'LIBRETRANSLATE_URL'), ('google_translate_api_key', 'GOOGLE_TRANSLATE_API_KEY'),
                ('deepl_api_key', 'DEEPL_API_KEY'), ('mymemory_email', 'MYMEMORY_EMAIL'),
                ('listmonk_url', 'LISTMONK_URL'), ('listmonk_api_user', 'LISTMONK_API_USER'),
                ('listmonk_api_token', 'LISTMONK_API_TOKEN'), ('anthropic_api_key', 'ANTHROPIC_API_KEY'),
            )
        },
    })


@staff_required
@require_POST
def test_translation(request):
    """HTMX: translate a sample sentence and report which provider answered."""
    sample = 'Thank you for volunteering with us this weekend!'  # source text is always English
    try:
        (result,), provider = auto_translate.translate_with_provider([sample], 'en', request.POST.get('target', 'es'))
        context = {'ok': True, 'provider': provider, 'sample': sample, 'result': result}
    except auto_translate.TranslationError as exc:
        context = {'ok': False, 'error': str(exc)}
    return render(request, 'console/partials/translation_test_result.html', context)


@staff_required
@require_POST
def test_email(request):
    """HTMX: send a test message to the signed-in admin."""
    if not request.user.email:
        messages.error(request, _('Add an email address to your account first.'))
        return HttpResponse(status=204)
    try:
        send_mail(
            _('Town Hall test email'),
            _('Your email settings work. Invitations and password resets will be delivered through listmonk.'),
            None, [request.user.email],
        )
    except Exception as exc:  # show the reason (listmonk's message, or a connection error) to the admin
        messages.error(request, _('Sending failed: %(error)s') % {'error': exc})
    else:
        messages.success(request, _('Test email sent to %(email)s.') % {'email': request.user.email})
    return HttpResponse(status=204)


# ---------------------------------------------------------------------------
# Bell Tower (see base/belltower.py for the handshake)
# ---------------------------------------------------------------------------

BELLTOWER_SESSION_KEY = 'belltower_connect'


@staff_required
@require_POST
def belltower_connect(request):
    """Find the Bell Tower server at the address typed in, then send the browser to
    its authorize page. The state and callback URL wait in the session for the reply."""
    try:
        base_url = belltower.normalize_url(request.POST.get('belltower_url', ''))
        endpoints = belltower.discover(base_url)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('console_settings_backend')
    except belltower.BellTowerError as exc:
        messages.error(request, _("Couldn't find Bell Tower there: %(error)s") % {'error': exc})
        return redirect('console_settings_backend')
    state = secrets.token_urlsafe(32)
    redirect_uri = request.build_absolute_uri(reverse('console_belltower_callback'))
    request.session[BELLTOWER_SESSION_KEY] = {
        'state': state, 'url': base_url, 'endpoints': endpoints, 'redirect_uri': redirect_uri,
    }
    return redirect(belltower.authorize_url(endpoints, redirect_uri, state))


@staff_required
def belltower_callback(request):
    """Bell Tower sends the browser back here with ?code=…&state=… (or ?error=…)."""
    pending = request.session.pop(BELLTOWER_SESSION_KEY, None)
    state = request.GET.get('state', '')
    if not pending or not state or not secrets.compare_digest(state, pending['state']):
        messages.error(request, _("This connection request expired or didn't start here. Please connect again."))
    elif request.GET.get('error') or not request.GET.get('code'):
        messages.warning(request, _('Bell Tower connection cancelled.'))
    else:
        try:
            result = belltower.exchange_code(pending['endpoints'], request.GET['code'], pending['redirect_uri'])
        except belltower.BellTowerError as exc:
            messages.error(request, _("Bell Tower didn't accept the connection: %(error)s") % {'error': exc})
        else:
            belltower.save_connection(pending['url'], pending['endpoints'], result['api_key'], result['username'])
            messages.success(request, _('Connected to Bell Tower as %(user)s.') % {'user': result['username']})
            # Link everyone to their Bell Tower account (matched by email, created if missing).
            belltower.run_after_commit(belltower.link_all_users)
    return redirect('console_settings_backend')


@staff_required
@require_POST
def belltower_link_users(request):
    """Link every user to their Bell Tower account again (e.g. after making Town Hall's
    Bell Tower account staff). Runs in the background."""
    belltower.run_after_commit(belltower.link_all_users)
    messages.success(request, _('Linking accounts with Bell Tower in the background. Reload this page in a minute to see the count.'))
    return redirect('console_settings_backend')


@staff_required
@require_POST
def belltower_disconnect(request):
    if belltower.disconnect():
        messages.success(request, _('Disconnected from Bell Tower.'))
    else:
        messages.warning(request, _(
            "Disconnected. Bell Tower couldn't be reached to revoke Town Hall's key; "
            "you can remove it under Connected apps on your Bell Tower account page."
        ))
    return redirect('console_settings_backend')
