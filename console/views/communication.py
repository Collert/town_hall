from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from base import listmonk, triggers
from base.models import SiteSettings

from ..decorators import staff_required
from ..forms import EmailSettingsForm


@staff_required
def email_settings(request):
    """For each app event: on/off, the listmonk template per language, and campaign options."""
    configured = listmonk.is_configured()
    options, error = None, None
    if configured:
        try:
            options = {'lists': listmonk.get_lists(), **listmonk.get_templates()}
        except listmonk.ListmonkError as exc:
            error = str(exc)

    if request.method == 'POST':
        form = EmailSettingsForm(request.POST, options=options)
        if form.is_valid():
            form.save()
            messages.success(request, _('Email settings saved.'))
            return redirect('console_communication_email')
        messages.error(request, _('Please fix the highlighted fields.'))
    else:
        form = EmailSettingsForm(options=options)

    site = SiteSettings.get_settings()
    lists = dict(options['lists']) if options else {}
    return render(request, 'console/communication_email.html', {
        'form': form,
        'configured': configured,
        'connected': options is not None,
        'error': error,
        'listmonk_url': listmonk.config()['url'],
        'users_list': lists.get(site.listmonk_list_id) if site.listmonk_list_id else None,
        'missing': form.missing_count(),
        'task': listmonk.current_task(),
        'task_error': listmonk.last_error(),
    })


@staff_required
def task_status(request):
    """HTMX poll while a background listmonk job runs; reloads the page when it's done."""
    task = listmonk.current_task()
    if not task:
        return HttpResponse(headers={'HX-Refresh': 'true'})
    return render(request, 'console/partials/listmonk_task.html', {'task': task})


@staff_required
@require_POST
def seed_missing(request):
    """HTMX: create the Town Hall templates that are missing in listmonk."""
    listmonk.run_in_background(_('Creating missing templates'), listmonk.seed_templates)
    return HttpResponse(headers={'HX-Refresh': 'true'})


@staff_required
@require_POST
def reset_template(request, key):
    """HTMX: re-render one event's Town Hall templates (every language) from the defaults."""
    if key not in triggers.BY_KEY:
        raise Http404
    try:
        listmonk.seed_templates(keys=[key], overwrite=True)
    except listmonk.ListmonkError as exc:
        messages.error(request, _('Reset failed: %(error)s') % {'error': exc})
        return HttpResponse(status=204)
    messages.success(request, _('"%(event)s" templates were reset to the Town Hall defaults.') % {
        'event': triggers.BY_KEY[key].label})
    return HttpResponse(headers={'HX-Refresh': 'true'})


@staff_required
@require_POST
def sync_users(request):
    """HTMX: push every active user with an email address to "Town Hall users"."""
    listmonk.run_in_background(_('Syncing users'), lambda: '%d synced, %d failed' % listmonk.sync_all()[:2])
    messages.info(request, _('Syncing users to listmonk in the background.'))
    return HttpResponse(status=204)
