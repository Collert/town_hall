import math
import os

from django.contrib import messages
from django.core.mail import send_mail
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from django.utils.translation import gettext as _, gettext_lazy

from base import listmonk
from django.db import transaction

from base import points
from base.models import Level, PointsRules, Profile, SiteSettings

from ..decorators import staff_required
from .. import auto_translate
from ..forms import BackendSettingsForm, LevelFormSet, OrganizationForm, PointsRulesForm

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
        'env_overrides': {
            name: bool(os.environ.get(env)) for name, env in (
                ('libretranslate_url', 'LIBRETRANSLATE_URL'), ('google_translate_api_key', 'GOOGLE_TRANSLATE_API_KEY'),
                ('deepl_api_key', 'DEEPL_API_KEY'), ('mymemory_email', 'MYMEMORY_EMAIL'),
                ('listmonk_url', 'LISTMONK_URL'), ('listmonk_api_user', 'LISTMONK_API_USER'),
                ('listmonk_api_token', 'LISTMONK_API_TOKEN'),
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

