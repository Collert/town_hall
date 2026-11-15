"""Organization > API: create and revoke API keys for other applications (api/)."""
from datetime import datetime, time

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from api.models import APIKey, ShiftHeartbeat
from api.shifts import HEARTBEAT_TIMEOUT
from jobs.models import Role

from ..decorators import staff_required

NEW_KEY = 'api_key_new'  # session: the plaintext of the key just made, shown once


@staff_required
def api_keys(request):
    permanent_roles = Role.objects.filter(permanent=True).order_by('name')
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()[:100]
        role_ids = request.POST.getlist('roles')
        roles = list(permanent_roles.filter(pk__in=[r for r in role_ids if r.isdigit()]))
        expires_at = None
        if request.POST.get('expires'):
            try:
                day = datetime.strptime(request.POST['expires'], '%Y-%m-%d').date()
                expires_at = timezone.make_aware(datetime.combine(day, time.max))
            except ValueError:
                pass
        if not name:
            messages.error(request, _('Give the key a name, so you know later which application uses it.'))
        elif expires_at and expires_at <= timezone.now():
            messages.error(request, _('The expiry date must be in the future.'))
        else:
            key, plaintext = APIKey.create(name, created_by=request.user, roles=roles, expires_at=expires_at)
            request.session[NEW_KEY] = {'id': key.pk, 'key': plaintext}
            messages.success(request, _('API key “%(name)s” created.') % {'name': name})
            return redirect('console_settings_api')

    new_key = request.session.pop(NEW_KEY, None)
    keys = list(APIKey.objects.select_related('created_by__profile').prefetch_related('roles'))
    return render(request, 'console/api_keys.html', {
        'new_key': new_key,
        'active_keys': [k for k in keys if k.is_active],
        'inactive_keys': [k for k in keys if not k.is_active],
        'permanent_roles': permanent_roles,
        'api_url': request.build_absolute_uri('/api/v1/'),
        'site_url': request.build_absolute_uri('/').rstrip('/'),
        'tracked': ShiftHeartbeat.objects.filter(shift__end_time__isnull=True).select_related(
            'shift__user__profile', 'shift__role', 'api_key').order_by('-last_seen'),
        'timeout_minutes': int(HEARTBEAT_TIMEOUT.total_seconds() // 60),
    })


@staff_required
@require_POST
def api_key_revoke(request, key_id):
    key = get_object_or_404(APIKey, pk=key_id)
    key.revoke()
    messages.success(request, _('“%(name)s” is revoked. Applications using it can no longer reach Town Hall.') % {'name': key.name})
    return redirect('console_settings_api')
