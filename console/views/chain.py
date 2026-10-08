"""Chain of command on the Roles & Staffing tab: areas, area leads, and editing slots
(times, headcount, area and shift lead). The rules live in events/leadership.py."""
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.models import User
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from base import belltower
from events import belltower_sync, leadership
from events.models import Event, EventArea, EventRoleSlot
from jobs.models import Role

from ..decorators import staff_required
from ..utils import AREA_ICONS, with_custom_icon
from .events import _roles_context


def _local_input(dt):
    """A datetime as a datetime-local input value in the current timezone."""
    return timezone.localtime(dt).strftime('%Y-%m-%dT%H:%M')


def _parse_local(value):
    try:
        return timezone.make_aware(datetime.strptime(value or '', '%Y-%m-%dT%H:%M'), timezone.get_current_timezone())
    except ValueError:
        return None


def _back(event):
    return redirect('console_event_roles', event_id=event.pk)


def _cards(request, event):
    return render(request, 'console/partials/event_role_cards.html', _roles_context(event, request.user))


def _queue(job, *args):
    if belltower.is_connected():
        belltower.run_after_commit(job, *args)


# ---------------------------------------------------------------------------
# Turning the chain of command on and off
# ---------------------------------------------------------------------------

@staff_required
@require_POST
def chain_toggle(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    enable = request.POST.get('enable') == '1'
    if enable == event.chain_of_command:
        return _back(event)
    if enable:
        Event.objects.filter(pk=event.pk).update(chain_of_command=True)
        messages.success(request, _('Chain of command is on. Create areas, add roles to them, and assign area leads.'))
        return _back(event)

    if not leadership.can_manage_leads(request.user, event):
        messages.error(request, _("Only the event's coordinators can remove its chain of command."))
        return _back(event)
    with transaction.atomic():
        areas = list(event.areas.all())
        leadership.remove_area_leads(areas)  # their "Area lead" shifts go
        event.areas.all().delete()  # volunteer slots stay, without an area
        Event.objects.filter(pk=event.pk).update(chain_of_command=False)
        event.chain_of_command = False
        for slot in event.role_slots.filter(lead__isnull=False).select_related('event', 'role'):
            if not leadership.has_lead_position(slot, event):
                leadership.set_slot_lead(slot, None)
    messages.info(request, _('Chain of command removed. Roles are back in one pool.'))
    return _back(event)


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------

@staff_required
def area_edit(request, event_id, area_id=None):
    event = get_object_or_404(Event, pk=event_id, chain_of_command=True)
    area = get_object_or_404(EventArea, pk=area_id, event=event) if area_id else None
    if request.method == 'POST':
        data = with_custom_icon(request.POST)
        name = data.get('name', '').strip()[:100]
        if not name:
            messages.error(request, _('Give the area a name, e.g. "Kitchen".'))
            return _back(event)
        if area is None:
            area = EventArea(event=event, order=event.areas.count())
        area.name = name
        area.icon = data.get('icon', '').strip()[:50]
        area.save()
        messages.success(request, _('Area "%(name)s" saved.') % {'name': area.name})
        return _back(event)
    return render(request, 'console/partials/area_dialog.html', {
        'event': event, 'area': area, 'icons': AREA_ICONS, 'current_icon': area.icon if area else AREA_ICONS[0],
    })


@staff_required
@require_POST
def area_delete(request, event_id, area_id):
    event = get_object_or_404(Event, pk=event_id)
    area = get_object_or_404(EventArea, pk=area_id, event=event)
    if area.lead_slots().exists() and not leadership.can_manage_leads(request.user, event):
        messages.error(request, _("Only the event's coordinators can remove an area that has leads."))
        return _cards(request, event)
    with transaction.atomic():
        leadership.remove_area_leads([area])
        area.delete()
    messages.success(request, _('Area "%(name)s" removed. Its roles are now unassigned.') % {'name': area.name})
    return _cards(request, event)


# ---------------------------------------------------------------------------
# Area leads (assigned by the general coordinator only)
# ---------------------------------------------------------------------------

def _lead_dialog_context(event, area, lead, query=''):
    coverage = leadership.area_coverage(area)
    if lead:
        start, end = lead.start_time, lead.end_time
    elif coverage['gaps']:
        start, end = coverage['gaps'][0]
    else:
        start, end = coverage['start'], coverage['end']
    return {
        'event': event, 'area': area, 'lead': lead, 'coverage': coverage,
        'candidates': leadership.area_lead_candidates(event, query),
        'start': _local_input(start), 'end': _local_input(end),
        'selected': lead.person.pk if lead and lead.person else None,
    }


def _lead_slot(area, lead_id):
    """An area lead's shift in ``area`` (the URL's lead_id is the slot's id)."""
    return get_object_or_404(EventRoleSlot.objects.prefetch_related('signups'), pk=lead_id, area=area,
                             role__system_key=Role.AREA_LEAD)


def _lead_id(lead):
    return lead.person.pk if lead and lead.person else None


@staff_required
def area_lead_edit(request, event_id, area_id, lead_id=None):
    event = get_object_or_404(Event, pk=event_id, chain_of_command=True)
    area = get_object_or_404(EventArea, pk=area_id, event=event)
    lead = _lead_slot(area, lead_id) if lead_id else None
    if not leadership.can_manage_leads(request.user, event):
        messages.error(request, _("Area leads are assigned by the event's general coordinator."))
        return _back(event)
    if lead and not leadership.can_change_lead(event, _lead_id(lead)):
        messages.error(request, _('Leads are locked 24 hours before the event starts.'))
        return _back(event)

    if request.method == 'POST':
        user = User.objects.filter(pk=request.POST.get('user') or 0, is_active=True).first()
        start, end = _parse_local(request.POST.get('start')), _parse_local(request.POST.get('end'))
        if not user:
            messages.error(request, _('Pick who leads the area.'))
        elif not (start and end) or end <= start:
            messages.error(request, _("The lead's shift must end after it starts."))
        else:
            busy = (EventRoleSlot.objects.filter(event=event, signups=user, start_time__lt=end, end_time__gt=start)
                    .exclude(pk=lead.pk if lead else None).select_related('role').first())
            with transaction.atomic():
                leadership.assign_area_lead(area, user, start, end, lead)
            if busy:
                messages.warning(request, _('%(name)s is also signed up as %(role)s at that time.') % {
                    'name': user.get_full_name() or user.username, 'role': busy.role.name})
            messages.success(request, _('%(name)s leads %(area)s.') % {'name': user.get_full_name() or user.username, 'area': area.name})
        return _back(event)

    if request.GET.get('partial') == 'candidates':
        return render(request, 'console/partials/area_lead_candidates.html',
                      _lead_dialog_context(event, area, lead, request.GET.get('q', '').strip()))
    return render(request, 'console/partials/area_lead_dialog.html', _lead_dialog_context(event, area, lead))


@staff_required
@require_POST
def area_lead_delete(request, event_id, area_id, lead_id):
    event = get_object_or_404(Event, pk=event_id)
    lead = _lead_slot(get_object_or_404(EventArea, pk=area_id, event=event), lead_id)
    if not leadership.can_manage_leads(request.user, event):
        messages.error(request, _("Area leads are assigned by the event's general coordinator."))
    elif not leadership.can_change_lead(event, _lead_id(lead)):
        messages.error(request, _('Leads are locked 24 hours before the event starts.'))
    else:
        with transaction.atomic():
            leadership.remove_area_lead(lead)
        messages.success(request, _('Area lead removed.'))
    return _cards(request, event)


# ---------------------------------------------------------------------------
# Editing a slot (time, headcount, area, shift lead)
# ---------------------------------------------------------------------------

@staff_required
def slot_edit(request, event_id, slot_id):
    event = get_object_or_404(Event, pk=event_id)
    slot = get_object_or_404(EventRoleSlot.objects.select_related('role', 'area', 'lead'), pk=slot_id, event=event,
                             role__system_key__isnull=True)  # area lead shifts are edited from the lead strip
    can_manage = leadership.can_manage_leads(request.user, event)
    lead_open = leadership.can_change_lead(event, slot.lead_id)

    if request.method == 'POST':
        start, end = _parse_local(request.POST.get('start')), _parse_local(request.POST.get('end'))
        try:
            qty = max(1, int(request.POST.get('qty') or 1))
            over = max(0, int(request.POST.get('over') or 0))
        except ValueError:
            qty = over = None
        signed_up = slot.signups.count()
        if not (start and end) or end <= start:
            messages.error(request, _('The time slot must end after it starts.'))
            return _back(event)
        if qty is None:
            messages.error(request, _('Enter how many volunteers the slot needs.'))
            return _back(event)
        if qty + over < signed_up:
            messages.error(request, _('%(n)d volunteers are already signed up; leave room for them.') % {'n': signed_up})
            return _back(event)

        old_area_id = slot.area_id
        slot.start_time, slot.end_time = start, end
        slot.required_qty, slot.allowed_overstaffing_qty = qty, over
        slot.is_public = request.POST.get('is_public') == 'on'
        if event.chain_of_command:
            slot.area = event.areas.filter(pk=request.POST.get('area') or 0).first()
        slot.save()
        if slot.area_id != old_area_id:
            _queue(belltower_sync.slot_moved, slot.pk, old_area_id)

        if 'lead' in request.POST and can_manage and leadership.has_lead_position(slot, event):
            wanted = request.POST.get('lead')
            new_lead = slot.signups.filter(pk=wanted).first() if wanted else None
            if (new_lead.pk if new_lead else None) != slot.lead_id:
                if lead_open:
                    leadership.set_slot_lead(slot, new_lead)
                else:
                    messages.error(request, _('Leads are locked 24 hours before the event starts.'))
        messages.success(request, _('Time slot saved.'))
        return _back(event)

    return render(request, 'console/partials/slot_edit_dialog.html', {
        'event': event,
        'slot': slot,
        'start': _local_input(slot.start_time),
        'end': _local_input(slot.end_time),
        'areas': event.areas.all() if event.chain_of_command else [],
        'lead_position': leadership.has_lead_position(slot, event),
        'candidates': leadership.ranked_signups(slot),
        'can_manage': can_manage,
        'lead_open': lead_open,
        'auto_assign_at': leadership.auto_assign_at(event),
        'lock_at': leadership.lock_at(event),
        'lead_min': leadership.SLOT_LEAD_MIN,
    })
