import time

from django.contrib import messages
from django.contrib.auth.models import User
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from base import listmonk, push, triggers
from base.models import PushSubscription, SiteSettings

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


@staff_required
def notification_settings(request):
    """Push notification categories on/off, devices, and the listmonk messenger setup."""
    if request.method == 'POST':
        listmonk._update_site(push_categories={c.key: f'push_{c.key}' in request.POST for c in push.CATEGORIES})
        messages.success(request, _('Notification settings saved.'))
        return redirect('console_communication_notifications')

    messenger_url = request.build_absolute_uri(reverse('push_listmonk'))
    configured = listmonk.is_configured()
    status, error = None, None
    if configured:
        try:
            status = push.listmonk_status(messenger_url)
        except listmonk.ListmonkError as exc:
            error = str(exc)
    devices = PushSubscription.objects.filter(user__is_active=True)
    return render(request, 'console/communication_notifications.html', {
        'categories': [(c, push.is_on(c.key)) for c in push.CATEGORIES],
        'device_count': devices.count(),
        'people_count': devices.values('user').distinct().count(),
        'my_device_count': devices.filter(user=request.user).count(),
        'configured': configured,
        'status': status,
        'error': error,
        'listmonk_url': listmonk.config()['url'],
        'messenger_url': messenger_url,
        'messenger_name': push.MESSENGER_NAME,
        'messenger_user': push.MESSENGER_USER,
        'messenger_password': push.messenger_password(),
        'attrib': push.ATTRIB,
        'template_name': push.TEMPLATE_NAME,
        'task': listmonk.current_task(),
        'task_error': listmonk.last_error(),
    })


@staff_required
@require_POST
def notification_test(request):
    """HTMX: push a test notification to the staff member's own devices."""
    sent = push.send_test(request.user)
    if sent:
        messages.success(request, _('Test notification sent to %(count)d device(s).') % {'count': sent})
    else:
        messages.error(request, _('None of your devices took the notification. Turn notifications on in Account settings first.'))
    return HttpResponse(status=204)


def _sync_push_users():
    time.sleep(5)  # listmonk reloads after its settings are saved
    synced = 0
    for user in User.objects.filter(is_active=True, push_subscriptions__isnull=False).exclude(email='').distinct():
        listmonk.sync_user(user)
        synced += 1
    return f'{synced} user(s) with notifications synced'


@staff_required
@require_POST
def notification_listmonk_setup(request):
    """HTMX: create the campaign template and register the Town Hall messenger in listmonk,
    then copy everyone's push endpoints into their subscriber attributes."""
    try:
        push.setup_listmonk(request.build_absolute_uri(reverse('push_listmonk')), request.build_absolute_uri('/'))
    except listmonk.ListmonkError as exc:
        messages.error(request, _('listmonk setup failed: %(error)s') % {'error': exc})
        return HttpResponse(headers={'HX-Refresh': 'true'})
    listmonk.run_in_background(_('Syncing people with notifications'), _sync_push_users)
    messages.success(request, _('listmonk is set up for notifications. It reloads its settings, so give it a few seconds.'))
    return HttpResponse(headers={'HX-Refresh': 'true'})
