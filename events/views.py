from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from base.email import notify
from base.models import Endorsement
from base.utils import qr_svg, short_datetime
from education.models import TrainingModule, TrainingModuleCompletion
from jobs.models import RoleTrainingRequirement, Shift

from .models import Event, EventCategory, EventFeedback, EventRoleSlot, EventSlotInvite

User = get_user_model()


def _visible_slots(event, user):
    """Public slots, plus invite-only slots the user is signed up or invited for."""
    slots = event.role_slots.select_related('role')
    if user.is_authenticated and user.is_staff:
        return slots
    if not user.is_authenticated:
        return slots.filter(is_public=True)
    return slots.filter(Q(is_public=True) | Q(signups=user) | Q(invites__user=user)).distinct()


def explore_events(request):
    events = Event.search_events(
        query=request.GET.get('q', ''),
        date_filter=request.GET.get('date'),
        category=request.GET.get('category'),
        user_lat=request.GET.get('lat'),
        user_lon=request.GET.get('lng'),
        distance=request.GET.get('distance'),
    )

    upcoming = Event.objects.filter(end_date__gte=timezone.now(), published=True)
    open_slots = EventRoleSlot.objects.filter(event__in=upcoming, is_public=True).annotate(n=Count('signups'))
    total_open_roles = sum(max(0, slot.required_qty - slot.n) for slot in open_slots)

    # Trending events based on cached view counts
    trending = sorted(upcoming, key=lambda e: cache.get(f'event_view_count_{e.pk}') or 0, reverse=True)[:2]

    return render(request, 'events/explore_events.html', {
        'events': events,
        'total_open_roles': total_open_roles,
        'categories': EventCategory.objects.all(),
        'trending_events': trending,
    })


def search_events(request):
    events = Event.search_events(
        query=request.GET.get('q', ''),
        date_filter=request.GET.get('date'),
        category=request.GET.get('category'),
        user_lat=request.GET.get('lat'),
        user_lon=request.GET.get('lng'),
        distance=request.GET.get('distance'),
    )
    return render(request, 'events/partials/events_grid.html', {'events': events})


def event_detail(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    if not event.published and not request.user.is_staff:
        raise Http404

    # Increment view count in cache
    cache_key = f'event_view_count_{event.pk}'
    try:
        cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, timeout=60 * 60 * 3)  # Cache for 3 hours

    user = request.user
    all_role_slots = list(_visible_slots(event, user).order_by('start_time'))

    # One card per role; the first slot of each role stands in for the group.
    unique_role_slots = []
    seen_roles = {}
    for slot in all_role_slots:
        signed_up = slot.user_signed_up(user) if user.is_authenticated else False
        if slot.role_id not in seen_roles:
            seen_roles[slot.role_id] = slot
            slot.user_signed_up_any = signed_up
            slot.total_available_spots = slot.available_slots()
            slot.multiple_slots = False
            slot.all_fully_staffed = slot.is_fully_staffed()
            unique_role_slots.append(slot)
        else:
            first_slot = seen_roles[slot.role_id]
            first_slot.user_signed_up_any = first_slot.user_signed_up_any or signed_up
            first_slot.total_available_spots += slot.available_slots()
            first_slot.multiple_slots = True
            first_slot.all_fully_staffed = first_slot.all_fully_staffed and slot.is_fully_staffed()

    completed_modules = set(
        TrainingModuleCompletion.objects.filter(user=user).values_list('training_module', flat=True)
    ) if user.is_authenticated else set()

    role_slots_data = {}
    for slot in all_role_slots:
        same_role = [s for s in all_role_slots if s.role_id == slot.role_id]
        timeslot_data = [{
            'id': s.pk,
            'start_time': s.start_time,
            'end_time': s.end_time,
            'available': s.available_slots(),
            'is_full': s.is_fully_staffed(),
            'is_selected': s.pk == slot.pk,
            'user_signed_up': s.user_signed_up(user) if user.is_authenticated else False,
        } for s in same_role]

        mandatory_module_ids = set(RoleTrainingRequirement.objects.filter(
            role=slot.role, mandatory=True,
        ).values_list('training_module_id', flat=True))

        training_data = []
        all_completed = True
        for mod in TrainingModule.objects.filter(roles=slot.role).distinct():
            is_completed = mod.id in completed_modules
            is_mandatory = mod.id in mandatory_module_ids
            if is_mandatory and not is_completed:
                all_completed = False
            training_data.append({
                'id': mod.pk,
                'title': mod.title,
                'completed': is_completed,
                'icon': mod.icon or 'school',
                'required': is_mandatory,
            })

        role_slots_data[slot.pk] = {
            'role_names': slot.role.name,
            'role_icons': [slot.role.icon] if slot.role.icon else [],
            'role_desc': slot.role.description,
            'slots': timeslot_data,
            'training': training_data,
            'all_completed': all_completed,
            'user_can_signup': (user.is_authenticated and all_completed and not slot.is_fully_staffed()
                                and not slot.user_has_conflict(user)),
        }

    return render(request, 'events/event_detail.html', {
        'event': event,
        'role_slots': unique_role_slots,
        'role_slots_data': role_slots_data,
        'event_volunteers': event.volunteers(),
    })


def _confirm_signup(request, slot):
    event = slot.event
    link = request.build_absolute_uri(reverse('opportunity_detail', args=[event.pk]))
    date = short_datetime(slot.start_time)
    location = event.full_location + (f' ({event.report_to_location})' if event.report_to_location else '')
    notify('signup_confirmed', request.user, {
        'event': event.title, 'role': slot.role.name, 'date': date, 'location': location, 'link': link,
    })


@login_required
@require_POST
def role_slot_signup(request, slot_id):
    # The time-slot picker posts the chosen slot as `slot_id`; it must be the same role at the same event.
    url_slot = get_object_or_404(EventRoleSlot, pk=slot_id)
    chosen = request.POST.get('slot_id')
    slot = url_slot
    if chosen and chosen.isdigit() and int(chosen) != url_slot.pk:
        slot = get_object_or_404(EventRoleSlot, pk=chosen, event=url_slot.event, role=url_slot.role)
    event_id = slot.event_id

    invite = EventSlotInvite.objects.filter(event_role_slot=slot, user=request.user, accepted=False).first()
    if not slot.is_public and not invite and not request.user.is_staff:
        messages.error(request, _('This time slot is invite-only.'))
        return redirect('opportunity_detail', event_id=event_id)

    if not slot.role.has_user_completed_required_training(request.user):
        messages.error(request, _("You haven't completed the required training modules for %(role)s.") % {'role': slot.role.name})
        return redirect('opportunity_detail', event_id=event_id)

    if slot.is_fully_staffed():
        messages.error(request, _('Sorry, this slot just filled up!'))
        return redirect('opportunity_detail', event_id=event_id)

    if slot.user_has_conflict(request.user):
        messages.error(request, _('You have other commitments that conflict with this slot.'))
        return redirect('opportunity_detail', event_id=event_id)

    slot.add_signup(request.user)
    if invite:
        invite.accepted = True
        invite.save(update_fields=['accepted'])
    _confirm_signup(request, slot)
    messages.success(request, _('Successfully signed up for %(role)s at %(event)s!') % {
        'role': slot.role.name, 'event': slot.event.title})
    return redirect('opportunity_detail', event_id=event_id)


def respond_to_invite(request, token):
    invite = get_object_or_404(EventSlotInvite.objects.select_related('event_role_slot__event', 'event_role_slot__role'), token=token)
    slot = invite.event_role_slot

    if not request.user.is_authenticated:
        messages.info(request, _('Log in to respond to your invitation.'))
        return redirect(f"{reverse('login')}?next={request.path}")
    if invite.user_id and invite.user_id != request.user.pk:
        raise Http404

    if request.method == 'POST':
        if request.POST.get('response') == 'decline':
            invite.delete()
            messages.info(request, _('Invitation declined. Thanks for letting us know.'))
            return redirect('explore_opportunities')
        if slot.user_signed_up(request.user):
            messages.info(request, _("You're already signed up for this slot."))
        elif not slot.role.has_user_completed_required_training(request.user):
            messages.error(request, _('Finish the required training first, then accept the invitation.'))
            return redirect('respond_to_invite', token=token)
        elif slot.overstaffing_slots_left() <= 0:
            messages.error(request, _('Sorry, this slot is already full.'))
        elif slot.user_has_conflict(request.user):
            messages.error(request, _('You have other commitments that conflict with this slot.'))
            return redirect('respond_to_invite', token=token)
        else:
            slot.add_signup(request.user)
            _confirm_signup(request, slot)
            messages.success(request, _("You're on the team! See you at %(event)s.") % {'event': slot.event.title})
        invite.accepted = True
        invite.save(update_fields=['accepted'])
        return redirect('opportunity_detail', event_id=slot.event_id)

    required = RoleTrainingRequirement.objects.filter(role=slot.role, mandatory=True).select_related('training_module')
    completed = set(TrainingModuleCompletion.objects.filter(user=request.user).values_list('training_module_id', flat=True))
    return render(request, 'events/respond_to_invite.html', {
        'invite': invite,
        'slot': slot,
        'event': slot.event,
        'training': [{'module': r.training_module, 'done': r.training_module_id in completed} for r in required],
        'training_ok': all(r.training_module_id in completed for r in required),
        'conflict': slot.user_has_conflict(request.user) and not slot.user_signed_up(request.user),
    })


def download_ics(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    response = HttpResponse(event.generate_ics(), content_type='text/calendar')
    response['Content-Disposition'] = f'attachment; filename="event-{event.pk}.ics"'
    return response


@login_required
def my_events(request):
    now = timezone.now()
    user = request.user

    # Upcoming: slots the user signed up for where the event hasn't ended yet
    upcoming_slots = (
        EventRoleSlot.objects
        .filter(signups=user, event__end_date__gte=now)
        .select_related('event__venue', 'role')
        .order_by('event__start_date', 'start_time')
    )

    # Group upcoming slots by event, keeping earliest slot per event for display
    upcoming_events_map = {}
    for slot in upcoming_slots:
        eid = slot.event.pk
        if eid not in upcoming_events_map:
            slot.event.is_today = timezone.localdate(slot.event.start_date) <= timezone.localdate() <= timezone.localdate(slot.event.end_date)
            upcoming_events_map[eid] = {'event': slot.event, 'slot': slot}

    upcoming_events = list(upcoming_events_map.values())
    today_shifts_count = sum(1 for item in upcoming_events if item['event'].is_today)

    # Past: slots the user signed up for where the event has already ended
    past_slots = (
        EventRoleSlot.objects
        .filter(signups=user, event__end_date__lt=now)
        .select_related('event__venue', 'role')
        .order_by('-event__end_date')
    )

    past_events_data = []
    seen_past = set()
    for slot in past_slots:
        eid = slot.event.pk
        if eid in seen_past:
            continue
        seen_past.add(eid)

        # Sum shift hours for this user on this event; fall back to slot duration
        shifts = Shift.objects.filter(user=user, event_role_slot__event=slot.event)
        total_hours = sum(s.duration() for s in shifts)
        if not total_hours:
            total_hours = (slot.end_time - slot.start_time).total_seconds() / 3600

        past_events_data.append({
            'event': slot.event,
            'slot': slot,
            'hours': round(total_hours, 1),
        })

    profile = getattr(user, 'profile', None)
    return render(request, 'events/my_events.html', {
        'upcoming_events': upcoming_events,
        'past_events': past_events_data,
        'today_shifts_count': today_shifts_count,
        'total_available': Event.objects.filter(end_date__gte=now, published=True).count(),
        'upcoming_count': len(upcoming_events),
        'impact_points': profile.impact_points if profile else 0,
        'pending_invites': EventSlotInvite.objects.filter(
            user=user, accepted=False, event_role_slot__event__end_date__gte=now,
        ).select_related('event_role_slot__event', 'event_role_slot__role'),
        'now': now,
    })


# ---------------------------------------------------------------------------
# After the event: thank-you report, certificate and shareable card
# ---------------------------------------------------------------------------

def _participation(event, user):
    """A volunteer's slots and shifts at an event, or None if they weren't part of it."""
    slots = list(event.role_slots.filter(signups=user).select_related('role'))
    shifts = list(Shift.objects.filter(event_role_slot__event=event, user=user, end_time__isnull=False))
    if not slots and not shifts:
        return None
    hours = sum(s.duration() for s in shifts)
    if not hours:
        hours = sum((s.end_time - s.start_time).total_seconds() / 3600 for s in slots)
    role = shifts[0].role if shifts and shifts[0].role else (slots[0].role if slots else None)
    return {
        'slots': slots,
        'shifts': shifts,
        'hours': round(hours, 1),
        'points': sum(s.end_impact_points or 0 for s in shifts),
        'role': role,
        'verified': bool(shifts),
    }


def _event_totals(event):
    shifts = list(Shift.objects.filter(event_role_slot__event=event, end_time__isnull=False))
    return {
        'hours': round(sum(s.duration() for s in shifts)),
        'volunteers': len({s.user_id for s in shifts}) or event.volunteers().count(),
    }


@login_required
def event_thank_you(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    participation = _participation(event, request.user)
    if not participation:
        messages.info(request, _("You weren't signed up for %(event)s.") % {'event': event.title})
        return redirect('my_events')

    feedback = EventFeedback.objects.filter(event=event, user=request.user).first()
    if request.method == 'POST':
        rating = request.POST.get('rating', '')
        if not (rating.isdigit() and 1 <= int(rating) <= 5):
            messages.error(request, _('Please choose a rating from 1 to 5 stars.'))
        else:
            EventFeedback.objects.update_or_create(event=event, user=request.user, defaults={
                'rating': int(rating),
                'enjoyed': request.POST.get('enjoyed', '').strip(),
                'suggestions': request.POST.get('suggestions', '').strip(),
            })
            messages.success(request, _('Thanks for sharing your experience!'))
        return redirect('event_thank_you', event_id=event.pk)

    teammates = list(event.volunteers().exclude(pk=request.user.pk).select_related('profile').prefetch_related('profile__skills')[:6])
    endorsed = set(Endorsement.objects.filter(endorser=request.user, endorsed__in=teammates).values_list('endorsed_id', 'skills'))
    role_by_user = {}
    for slot in event.role_slots.select_related('role').prefetch_related('signups'):
        for volunteer in slot.signups.all():
            role_by_user.setdefault(volunteer.pk, slot.role.name)
    from education.models import Skill
    fallback_skills = list(Skill.objects.annotate(n=Count('endorsements')).order_by('-n', 'name')[:2])
    teammate_cards = []
    for teammate in teammates:
        skills = list(teammate.profile.skills.all()[:2]) or fallback_skills
        teammate_cards.append({
            'user': teammate,
            'role': role_by_user.get(teammate.pk, ''),
            'skills': [{'skill': s, 'endorsed': (teammate.pk, s.pk) in endorsed} for s in skills],
        })

    return render(request, 'events/event_thank_you.html', {
        'event': event,
        'me': participation,
        'connections': max(0, event.volunteers().count() - 1),
        'teammates': teammate_cards,
        'feedback': feedback,
        'totals': _event_totals(event),
        'coordinator': event.coordinators.first(),
    })


def event_certificate(request, event_id, username):
    event = get_object_or_404(Event, pk=event_id)
    volunteer = get_object_or_404(User, username=username, is_active=True)
    participation = _participation(event, volunteer)
    if not participation or not event.is_past:
        raise Http404
    url = request.build_absolute_uri(reverse('event_certificate', args=[event.pk, volunteer.username]))
    endorsement = Endorsement.objects.filter(endorsed=volunteer, event=event).exclude(text='').first()
    return render(request, 'events/event_certificate.html', {
        'event': event,
        'volunteer': volunteer,
        'me': participation,
        'summary': endorsement.text if endorsement else event.post_event_statement,
        'supervisor': event.coordinators.first(),
        'certificate_id': f'VI-{event.start_date:%Y}-{event.pk:03d}{volunteer.pk:04d}',
        'qr_svg': qr_svg(url),
        'is_owner': request.user == volunteer,
    })


def event_impact_card(request, event_id):
    event = get_object_or_404(Event, pk=event_id, published=True)
    totals = _event_totals(event)
    scheduled = sum(
        (slot.end_time - slot.start_time).total_seconds() / 3600 * slot.signups.count()
        for slot in event.role_slots.all()
    )
    return render(request, 'events/event_impact_card.html', {
        'event': event,
        'totals': totals,
        'efficiency': min(100, round(totals['hours'] / scheduled * 100)) if scheduled else None,
        'share_url': request.build_absolute_uri(),
    })
