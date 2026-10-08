import csv
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from base import listmonk
from base.email import announce_event, notify, text_to_html
from base.models import Notification
from base.utils import short_datetime
from education.models import TrainingModuleCompletion
from events import leadership
from events.models import Event, EventArea, EventRoleSlot, EventSlotInvite
from jobs.models import Role, Shift

from ..decorators import staff_required
from ..forms import EventForm
from ..utils import activity_feed, page_window, paginate, role_staffing, slots_with_counts


def _is_htmx(request):
    return request.headers.get('HX-Request') == 'true'


# ---------------------------------------------------------------------------
# Event list
# ---------------------------------------------------------------------------

def _past_events_page(request):
    now = timezone.now()
    events = Event.objects.filter(end_date__lt=now).order_by('-end_date')
    query = request.GET.get('q', '').strip()
    if query:
        events = events.filter(Q(title__icontains=query) | Q(location__icontains=query))
    events = events.annotate(volunteer_count=Count('role_slots__signups', distinct=True))
    page = paginate(request, events, per_page=8)
    for event in page:
        event.hours = event.hours_logged
    return page, query


@staff_required
def event_list(request):
    now = timezone.now()
    live = Event.objects.filter(start_date__lte=now, end_date__gte=now).order_by('start_date')
    upcoming = Event.objects.filter(start_date__gt=now).order_by('start_date')
    page, query = _past_events_page(request)
    return render(request, 'console/event_list.html', {
        'live_events': live,
        'upcoming_events': upcoming[:6],
        'upcoming_total': upcoming.count(),
        'page': page,
        'pages': page_window(page),
        'query': query,
    })


@staff_required
def event_history(request):
    page, query = _past_events_page(request)
    return render(request, 'console/partials/event_history.html', {
        'page': page, 'pages': page_window(page), 'query': query,
    })


# ---------------------------------------------------------------------------
# Create / edit overview
# ---------------------------------------------------------------------------

@staff_required
def event_edit(request, event_id=None):
    event = get_object_or_404(Event, pk=event_id) if event_id else None
    if request.method == 'POST':
        form = EventForm(request.POST, request.FILES, instance=event)
        if form.is_valid():
            created = event is None
            was_published = bool(event and event.published)
            event = form.save()
            if event.published and not was_published:
                _announce_event(request, event)
            if created:
                event.coordinators.add(request.user)
                messages.success(request, _('Event created. Now add the roles you need filled.'))
                return redirect('console_event_roles', event_id=event.pk)
            messages.success(request, _('Event details saved.'))
            return redirect('console_event_edit', event_id=event.pk)
        messages.error(request, _('Please fix the highlighted fields.'))
    else:
        initial = {}
        if not event:
            start = (timezone.localtime() + timedelta(days=7)).replace(hour=9, minute=0, second=0, microsecond=0)
            initial = {'start_date': start, 'end_date': start.replace(hour=17), 'published': False}
        form = EventForm(instance=event, initial=initial)
    return render(request, 'console/event_edit.html', {'form': form, 'event': event})


# ---------------------------------------------------------------------------
# Roles & staffing
# ---------------------------------------------------------------------------

def _decorate_slots(event, slots, now):
    """Shift lead state for each slot's chip: position, lock, and who'd be picked."""
    for slot in slots:
        slot.lead_position = leadership.has_lead_position(slot, event)
        slot.lead_locked = slot.lead_position and not leadership.can_change_lead(event, slot.lead_id, now)
        slot.suggested_lead = leadership.suggested_lead(slot) if slot.lead_position and not slot.lead_id else None


def _roles_context(event, user):
    now = timezone.now()
    all_slots = list(slots_with_counts(
        event.role_slots.select_related('role', 'area', 'lead__profile').prefetch_related('signups__profile')
        .order_by('start_time')
    ))
    # Area lead shifts show in their area's lead strip, not as role cards.
    lead_slots = [s for s in all_slots if s.is_area_lead]
    slots = [s for s in all_slots if not s.is_area_lead]
    _decorate_slots(event, slots, now)
    staffing = role_staffing(event, slots)
    required = sum(r['required'] for r in staffing)
    filled = sum(min(r['filled'], r['required']) for r in staffing)
    critical = [r for r in staffing if r['status'] == 'critical']

    areas, unassigned = [], []
    if event.chain_of_command:
        for area in event.areas.all():
            area_slots = [s for s in slots if s.area_id == area.pk]
            leads = [s for s in lead_slots if s.area_id == area.pk and s.person]
            for lead in leads:
                lead.locked = not leadership.can_change_lead(event, lead.person.pk if lead.person else None, now)
            start = min((s.start_time for s in area_slots), default=event.start_date)
            end = max((s.end_time for s in area_slots), default=event.end_date)
            areas.append({
                'area': area, 'staffing': role_staffing(event, area_slots), 'leads': leads, 'start': start, 'end': end,
                'gaps': leadership.gaps(start, end, [(l.start_time, l.end_time) for l in leads]),
            })
        unassigned = role_staffing(event, [s for s in slots if s.area_id is None])

    return {
        'event': event,
        'staffing': staffing,
        'areas': areas,
        'unassigned': unassigned,
        'overall_percent': round(filled / required * 100) if required else 0,
        'shortfall': sum(r['missing'] for r in staffing),
        'critical': critical,
        'volunteer_count': event.volunteers().count(),
        'volunteers_needed': required,
        'suggest_chain': leadership.suggests_chain(event),
        'chain_threshold': leadership.SUGGEST_CHAIN_AT,
        'lead_positions': any(s.lead_position for s in slots),
        'lead_phase': leadership.phase(event, now),
        'auto_assign_at': leadership.auto_assign_at(event),
        'lock_at': leadership.lock_at(event),
        'can_manage_leads': leadership.can_manage_leads(user, event),
    }


@staff_required
def event_roles(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    return render(request, 'console/event_roles.html', _roles_context(event, request.user))


@staff_required
@require_POST
def delete_slot(request, event_id, slot_id):
    slot = get_object_or_404(EventRoleSlot, pk=slot_id, event_id=event_id)
    slot.delete()
    messages.success(request, _('Time slot removed.'))
    return render(request, 'console/partials/event_role_cards.html', _roles_context(slot.event, request.user))


def _announce_event(request, event):
    """'New event published' campaign, if that trigger is on, in the publishing admin's language."""
    link = request.build_absolute_uri(reverse('opportunity_detail', args=[event.pk]))
    try:
        result = announce_event(event, link, translation.get_language())
    except listmonk.ListmonkError as exc:
        messages.error(request, _('The listmonk announcement failed: %(error)s') % {'error': exc})
        return
    if result == 'sent':
        messages.success(request, _('Announcement emailed to "Town Hall users".'))
    elif result == 'draft':
        messages.info(request, _('An announcement campaign is waiting as a draft in listmonk.'))


@staff_required
@require_POST
def toggle_publish(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    event.published = not event.published
    event.save(update_fields=['published'])
    if event.published:
        _announce_event(request, event)
        messages.success(request, _('"%(title)s" is now visible to volunteers.') % {'title': event.title})
    else:
        messages.info(request, _('"%(title)s" is hidden from volunteers.') % {'title': event.title})
    return redirect(request.POST.get('next') or reverse('console_event_roles', args=[event.pk]))


@staff_required
@require_POST
def message_volunteers(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    text = request.POST.get('message', '').strip()
    if not text:
        messages.error(request, _('Write a message before sending.'))
        return redirect('console_event_roles', event_id=event.pk)
    link = request.build_absolute_uri(reverse('opportunity_detail', args=[event.pk]))
    volunteers = list(event.volunteers())
    Notification.objects.bulk_create([
        Notification(user=v, message=f'{event.title}: {text}'[:255], link=link) for v in volunteers
    ])
    for volunteer in volunteers:
        notify('event_message', volunteer, {
            'event': event.title, 'message': text, 'message_html': text_to_html(text), 'link': link,
        })
    messages.success(request, _('Message sent to %(count)d volunteers.') % {'count': len(volunteers)})
    return redirect('console_event_roles', event_id=event.pk)


def _parse_slot_rows(request, event):
    """Build unsaved EventRoleSlots from the repeated slot_* inputs."""
    tz = timezone.get_current_timezone()
    rows = zip(
        request.POST.getlist('slot_date'), request.POST.getlist('slot_start'),
        request.POST.getlist('slot_end'), request.POST.getlist('slot_qty'),
        request.POST.getlist('slot_over'),
    )
    slots, errors = [], []
    for index, (day, start, end, qty, over) in enumerate(rows, start=1):
        try:
            day = datetime.strptime(day, '%Y-%m-%d').date()
            start_dt = timezone.make_aware(datetime.combine(day, datetime.strptime(start, '%H:%M').time()), tz)
            end_dt = timezone.make_aware(datetime.combine(day, datetime.strptime(end, '%H:%M').time()), tz)
            qty, over = max(1, int(qty or 1)), max(0, int(over or 0))
        except ValueError:
            errors.append(_('Slot %(n)d has an invalid date or time.') % {'n': index})
            continue
        if end_dt <= start_dt:
            errors.append(_('Slot %(n)d must end after it starts.') % {'n': index})
            continue
        slots.append(EventRoleSlot(
            event=event, start_time=start_dt, end_time=end_dt,
            required_qty=qty, allowed_overstaffing_qty=over,
        ))
    if not slots and not errors:
        errors.append(_('Add at least one time slot.'))
    return slots, errors


def _role_options(query=''):
    roles = (Role.objects.filter(system_key__isnull=True)
             .annotate(module_count=Count('training_modules', distinct=True)).order_by('permanent', 'name'))
    if query:
        roles = roles.filter(Q(name__icontains=query) | Q(description__icontains=query))
    return roles


@staff_required
def add_role_slots(request, event_id):
    event = get_object_or_404(Event, pk=event_id)

    if request.method == 'POST':
        role = Role.objects.filter(pk=request.POST.get('role'), system_key__isnull=True).first()
        slots, errors = _parse_slot_rows(request, event)
        if not role:
            errors.insert(0, _('Select a role first.'))
        if errors:
            for error in errors:
                messages.error(request, error)
            return redirect('console_event_roles', event_id=event.pk)
        is_public = request.POST.get('is_public') == 'on'
        area = event.areas.filter(pk=request.POST.get('area') or 0).first() if event.chain_of_command else None
        suggested_before = leadership.suggests_chain(event)
        for slot in slots:
            slot.role = role
            slot.area = area
            slot.is_public = is_public
            slot.save()
        messages.success(request, _('Added %(role)s with %(count)d time slot(s).') % {'role': role.name, 'count': len(slots)})
        if not suggested_before and leadership.suggests_chain(event):
            messages.info(request, _('This event now needs more than %(n)d volunteers. Consider setting up a chain of command with areas and leads.')
                          % {'n': leadership.SUGGEST_CHAIN_AT})
        if request.POST.get('next') == 'invite':
            return redirect(f"{reverse('console_event_invite', args=[event.pk])}?slot={slots[0].pk}")
        return redirect('console_event_roles', event_id=event.pk)

    if request.GET.get('partial') == 'options':
        return render(request, 'console/partials/role_options.html', {
            'roles': _role_options(request.GET.get('q', '').strip()),
        })

    start = timezone.localtime(event.start_date)
    end = timezone.localtime(event.end_date)
    return render(request, 'console/partials/add_role_dialog.html', {
        'event': event,
        'area': event.areas.filter(pk=request.GET.get('area') or 0).first() if event.chain_of_command else None,
        'roles': _role_options(),
        'preselected': request.GET.get('role'),
        'default_slot': {
            'date': start.date().isoformat(),
            'start': start.strftime('%H:%M'),
            'end': end.strftime('%H:%M') if end.date() == start.date() else '17:00',
        },
    })


@staff_required
def slot_row(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    start = timezone.localtime(event.start_date)
    return render(request, 'console/partials/slot_row.html', {
        'default_slot': {'date': start.date().isoformat(), 'start': start.strftime('%H:%M'), 'end': '17:00'},
    })


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------

def _recipients(slot, query=''):
    """Volunteers ranked by how well they fit the slot's role."""
    role = slot.role
    preferred = set(role.preferred_skills.values_list('pk', flat=True))
    required_modules = set(role.training_modules.values_list('pk', flat=True))
    invited = set(slot.invites.filter(accepted=False).values_list('user_id', flat=True))

    users = (User.objects.filter(is_active=True)
             .exclude(pk__in=slot.signups.values_list('pk', flat=True))
             .select_related('profile').prefetch_related('profile__skills'))
    if query:
        users = users.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query)
                             | Q(username__icontains=query) | Q(email__icontains=query)
                             | Q(profile__skills__name__icontains=query)).distinct()

    completed = {}
    for user_id, module_id in TrainingModuleCompletion.objects.filter(
            user__in=users, training_module__in=required_modules).values_list('user_id', 'training_module_id'):
        completed.setdefault(user_id, set()).add(module_id)
    experienced = set(Shift.objects.filter(role=role).values_list('user_id', flat=True))

    ranked = []
    for user in users[:200]:
        skills = list(user.profile.skills.all()) if hasattr(user, 'profile') else []
        matched = [s for s in skills if s.pk in preferred]
        score = 40
        if preferred:
            score += round(30 * len(matched) / len(preferred))
        else:
            score += 15
        if required_modules:
            score += round(20 * len(completed.get(user.pk, ())) / len(required_modules))
        else:
            score += 20
        if user.pk in experienced:
            score += 10
        ranked.append({
            'user': user,
            'score': min(score, 99),
            'recommended': score >= 75,
            'matched': matched or skills[:2],
            'invited': user.pk in invited,
            'conflict': slot.user_has_conflict(user),
        })
    ranked.sort(key=lambda r: (-r['score'], r['user'].first_name))
    return ranked


@staff_required
def invite_volunteers(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    slots = list(event.role_slots.select_related('role').order_by('start_time'))
    if not slots:
        messages.info(request, _('Add a role to this event before inviting volunteers.'))
        return redirect('console_event_roles', event_id=event.pk)

    slot = next((s for s in slots if str(s.pk) == (request.POST.get('slot') or request.GET.get('slot'))), slots[0])

    if request.method == 'POST':
        user_ids = request.POST.getlist('user_ids')
        subject = request.POST.get('subject', '').strip() or _('You are invited to %(event)s') % {'event': event.title}
        body = request.POST.get('body', '').strip()
        users = User.objects.filter(pk__in=user_ids, is_active=True)
        sent = 0
        for user in users:
            invite = EventSlotInvite.objects.filter(event_role_slot=slot, user=user, accepted=False).first()
            if not invite:
                invite = EventSlotInvite.objects.create(event_role_slot=slot, user=user)
            link = request.build_absolute_uri(reverse('respond_to_invite', args=[invite.token]))
            name = user.first_name or user.username
            Notification.objects.create(user=user, message=subject[:255], link=link)
            message = body.replace('[Name]', name)
            notify('invitation', user, {
                'subject': subject, 'link': link, 'event': event.title, 'role': slot.role.name,
                'date': short_datetime(slot.start_time, user.profile.language),
                'message': message, 'message_html': text_to_html(message),
            })
            sent += 1
        if sent:
            messages.success(request, _('Sent %(count)d invitation(s) for %(role)s.') % {'count': sent, 'role': slot.role.name})
        else:
            messages.error(request, _('Select at least one volunteer to invite.'))
            return redirect(f"{reverse('console_event_invite', args=[event.pk])}?slot={slot.pk}")
        return redirect('console_event_roles', event_id=event.pk)

    default_body = _(
        "Hello [Name],\n\nYour skills would make a real difference at %(event)s. "
        "We're looking for a %(role)s on %(date)s and thought of you.\n\nWe'd love to have you on the team!"
    ) % {
        'event': event.title, 'role': slot.role.name,
        'date': short_datetime(slot.start_time),
    }
    return render(request, 'console/event_invite.html', {
        'event': event,
        'slots': slots,
        'slot': slot,
        'recipients': _recipients(slot),
        'default_subject': _('Be the spark at %(event)s!') % {'event': event.title},
        'default_body': default_body,
    })


@staff_required
def invite_recipients(request, event_id):
    slot = get_object_or_404(EventRoleSlot, pk=request.GET.get('slot'), event_id=event_id)
    return render(request, 'console/partials/invite_recipients.html', {
        'recipients': _recipients(slot, request.GET.get('q', '').strip()),
    })


# ---------------------------------------------------------------------------
# Live check-in monitor
# ---------------------------------------------------------------------------

def _monitor_context(event, action='', query=''):
    now = timezone.now()
    shifts = Shift.objects.filter(event_role_slot__event=event).select_related('user', 'role', 'event_role_slot__role')
    open_shifts = shifts.filter(end_time__isnull=True)
    checked_in_ids = set(open_shifts.values_list('user_id', flat=True))
    signed_up = event.total_signed_up()

    slots = list(slots_with_counts(event.role_slots.select_related('role')))
    soon = [s for s in slots if now <= s.start_time <= now + timedelta(hours=1)]
    expected_next_hour = sum(s.signup_count for s in soon)
    active_slots = [s for s in slots if s.start_time <= now <= s.end_time]

    by_role = {}
    for slot in slots:
        entry = by_role.setdefault(slot.role_id, {'role': slot.role, 'signed_up': 0, 'checked_in': 0})
        entry['signed_up'] += slot.signup_count
    for shift in open_shifts:
        role_id = shift.event_role_slot.role_id
        if role_id in by_role:
            by_role[role_id]['checked_in'] += 1
    staffing = list(by_role.values())
    for entry in staffing:
        entry['percent'] = round(entry['checked_in'] / entry['signed_up'] * 100) if entry['signed_up'] else 0
    shortages = sorted(
        (e for e in staffing if e['signed_up'] and e['percent'] < 50),
        key=lambda e: e['percent'],
    )

    log = []
    for shift in shifts.order_by('-start_time'):
        log.append({'shift': shift, 'action': 'in', 'time': shift.start_time})
        if shift.end_time:
            log.append({'shift': shift, 'action': 'out', 'time': shift.end_time})
    if action in ('in', 'out'):
        log = [entry for entry in log if entry['action'] == action]
    if query:
        q = query.lower()
        log = [e for e in log if q in (e['shift'].user.get_full_name() or e['shift'].user.username).lower()]
    log.sort(key=lambda e: e['time'], reverse=True)

    total_checked = len(checked_in_ids)
    return {
        'event': event,
        'checked_in': total_checked,
        'checked_in_percent': round(total_checked / signed_up * 100) if signed_up else 0,
        'signed_up': signed_up,
        'expected_next_hour': expected_next_hour,
        'next_arrival': min((s.start_time for s in soon), default=None),
        'active_roles': len({s.role_id for s in active_slots}),
        'staffing': staffing,
        'overall_percent': round(sum(e['checked_in'] for e in staffing) / signed_up * 100) if signed_up else 0,
        'shortage': shortages[0] if shortages else None,
        'log': log[:25],
        'log_total': len(log),
        'action': action,
        'query': query,
        'checked_in_ids': checked_in_ids,
    }


@staff_required
def event_monitor(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    context = _monitor_context(event)
    context['awaiting'] = (event.volunteers().exclude(pk__in=context['checked_in_ids'])
                           .order_by('first_name', 'username'))
    return render(request, 'console/event_monitor.html', context)


@staff_required
def monitor_activity(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    context = _monitor_context(event, request.GET.get('action', ''), request.GET.get('q', '').strip())
    return render(request, 'console/partials/monitor_activity.html', context)


@staff_required
@require_POST
def manual_check_in(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    if request.POST.get('shift_id'):
        shift = get_object_or_404(Shift, pk=request.POST['shift_id'], event_role_slot__event=event)
        shift.end_shift()
        messages.success(request, _('%(name)s checked out.') % {'name': shift.user.get_full_name() or shift.user.username})
        return redirect('console_event_monitor', event_id=event.pk)

    user = get_object_or_404(User, pk=request.POST.get('user_id'))
    if Shift.objects.filter(user=user, end_time__isnull=True).exists():
        messages.info(request, _('%(name)s is already checked in.') % {'name': user.get_full_name() or user.username})
        return redirect('console_event_monitor', event_id=event.pk)
    now = timezone.now()
    slots = event.role_slots.filter(signups=user)
    slot = (slots.filter(start_time__lte=now + timedelta(hours=1), end_time__gte=now).order_by('start_time').first()
            or slots.order_by('start_time').first())
    if not slot:
        messages.error(request, _('That volunteer is not signed up for this event.'))
        return redirect('console_event_monitor', event_id=event.pk)
    Shift.objects.create(user=user, event_role_slot=slot)
    messages.success(request, _('%(name)s checked in as %(role)s.') % {
        'name': user.get_full_name() or user.username, 'role': slot.role.name})
    return redirect('console_event_monitor', event_id=event.pk)


@staff_required
def export_shift_log(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="event-{event.pk}-shifts.csv"'
    writer = csv.writer(response)
    writer.writerow(['Volunteer', 'Email', 'Role', 'Checked in', 'Checked out', 'Hours', 'Impact points', 'Clutch'])
    for shift in event.shifts().select_related('user', 'role').order_by('start_time'):
        writer.writerow([
            shift.user.get_full_name() or shift.user.username, shift.user.email,
            shift.role.name if shift.role else '',
            timezone.localtime(shift.start_time).strftime('%Y-%m-%d %H:%M'),
            timezone.localtime(shift.end_time).strftime('%Y-%m-%d %H:%M') if shift.end_time else '',
            round(shift.duration(), 2), shift.end_impact_points or '', 'yes' if shift.clutched else '',
        ])
    return response


# ---------------------------------------------------------------------------
# Post-event report
# ---------------------------------------------------------------------------

@staff_required
def event_report(request, event_id):
    event = get_object_or_404(Event, pk=event_id)

    if request.method == 'POST':
        event.post_event_statement = request.POST.get('post_event_statement', '').strip()
        event.save(update_fields=['post_event_statement'])
        messages.success(request, _('Post-event statement saved.'))
        if _is_htmx(request):
            return HttpResponse(status=204)
        return redirect('console_event_report', event_id=event.pk)

    if request.GET.get('export'):
        return export_shift_log(request, event_id)

    from base.models import Endorsement
    from events.models import EventFeedback

    shifts = list(event.shifts().select_related('user', 'role'))
    hours_by_user, hours_by_role = {}, {}
    for shift in shifts:
        hours = shift.duration()
        hours_by_user[shift.user_id] = hours_by_user.get(shift.user_id, 0) + hours
        hours_by_role[shift.role_id] = hours_by_role.get(shift.role_id, 0) + hours

    roles = []
    scheduled_total = 0
    for entry in role_staffing(event):
        scheduled = sum(
            (slot.end_time - slot.start_time).total_seconds() / 3600 * slot.signup_count for slot in entry['slots']
        )
        scheduled_total += scheduled
        actual = hours_by_role.get(entry['role'].pk, 0)
        roles.append({
            'role': entry['role'],
            'hours': round(actual, 1),
            'efficiency': min(100, round(actual / scheduled * 100)) if scheduled else None,
        })
    actual_total = sum(hours_by_user.values())

    # Everyone who signed up or logged a shift (walk-ins and kiosk clutch covers have no sign-up).
    volunteers = list(User.objects.filter(
        Q(commitments__event=event) | Q(pk__in=hours_by_user.keys())
    ).distinct().select_related('profile'))
    top_skill = {}
    for endorsed_id, name in Endorsement.objects.filter(endorsed__in=volunteers).values_list('endorsed_id', 'skills__name'):
        if name:
            top_skill.setdefault(endorsed_id, {}).setdefault(name, 0)
            top_skill[endorsed_id][name] += 1
    role_of = {}
    for slot in event.role_slots.select_related('role').prefetch_related('signups'):
        for user in slot.signups.all():
            role_of.setdefault(user.pk, slot.role.name)
    for shift in shifts:
        if shift.role:
            role_of.setdefault(shift.user_id, shift.role.name)
    team = []
    for user in volunteers:
        skills = top_skill.get(user.pk, {})
        team.append({
            'user': user,
            'hours': round(hours_by_user.get(user.pk, 0), 1),
            'role': role_of.get(user.pk, ''),
            'top_skill': max(skills, key=skills.get) if skills else None,
        })
    team.sort(key=lambda t: -t['hours'])

    feedback = EventFeedback.objects.filter(event=event).select_related('user').order_by('-created_at')
    ratings = [f.rating for f in feedback]
    page = paginate(request, team[3:] if len(team) > 3 else [], per_page=8)

    return render(request, 'console/event_report.html', {
        'event': event,
        'efficiency': min(100, round(actual_total / scheduled_total * 100)) if scheduled_total else None,
        'volunteer_total': len(volunteers),
        'hours_total': round(actual_total, 1),
        'roles': roles,
        'top_performers': [t for t in team[:3] if t['hours'] > 0] or team[:3],
        'page': page,
        'pages': page_window(page),
        'feedback': feedback[:5],
        'average_rating': round(sum(ratings) / len(ratings), 1) if ratings else None,
        'feedback_count': len(ratings),
        'endorsed_ids': set(Endorsement.objects.filter(event=event, endorser=request.user).values_list('endorsed_id', flat=True)),
    })
