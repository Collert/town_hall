"""Self-service check-in kiosk.

A kiosk device is pointed at an event (/jobs/kiosk/event/<id>/) or a venue
(/jobs/kiosk/venue/<id>/). Volunteers identify themselves with their 6-digit ID
code or email + password. The kiosk never logs them into the site: their user id
is kept in the session under KIOSK_USER only while they're at the device.
"""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from base.models import Profile, SiteSettings, Venue
from base.utils import time_range
from console.decorators import staff_required
from events.models import Event

from .models import Shift

KIOSK_USER = 'kiosk_user_id'
EARLY_CHECK_IN = timedelta(hours=1)


def _place(kind, place_id):
    if kind == 'event':
        return get_object_or_404(Event, pk=place_id)
    if kind == 'venue':
        return get_object_or_404(Venue, pk=place_id)
    raise Http404


def _place_context(kind, place):
    """Data for the left-hand info card."""
    now = timezone.now()
    context = {'kind': kind, 'place': place, 'idle_timeout': SiteSettings.get_settings().kiosk_idle_timeout_seconds}
    if kind == 'event':
        today = timezone.localdate()
        todays_shifts = place.shifts().filter(start_time__date=today)
        hours_today = round(sum(((s.end_time or now) - s.start_time).total_seconds() for s in todays_shifts) / 3600)
        scheduled = sum(
            (slot.end_time - slot.start_time).total_seconds() / 3600 * slot.n
            for slot in place.role_slots.filter(start_time__date=today).annotate(n=Count('signups'))
        )
        context.update({
            'registered': place.volunteers().count(),
            'hours_today': hours_today,
            'hours_percent': min(100, round(hours_today / scheduled * 100)) if scheduled else 0,
            'on_shift': place.shifts().filter(end_time__isnull=True).count(),
        })
    else:
        context.update({
            'hours': place.operating_hours.order_by('day_of_week'),
            'todays_hours': place.operating_hours.filter(day_of_week=timezone.localdate().weekday()).first(),
            'notes': place.active_notes(),
        })
    return context


def _kiosk_user(request):
    user_id = request.session.get(KIOSK_USER)
    return User.objects.filter(pk=user_id, is_active=True).select_related('profile').first() if user_id else None


def _sign_in(request, kind, place, user):
    request.session[KIOSK_USER] = user.pk
    return redirect('kiosk_start', kind=kind, place_id=place.pk)


@staff_required
def kiosk_home(request):
    """Staff picks which event or venue this device serves."""
    now = timezone.now()
    return render(request, 'jobs/kiosk_home.html', {
        'events': Event.objects.filter(end_date__gte=now - timedelta(hours=12), start_date__lte=now + timedelta(days=1)).order_by('start_date'),
        'later_events': Event.objects.filter(start_date__gt=now + timedelta(days=1)).order_by('start_date')[:5],
        'venues': Venue.objects.annotate(role_count=Count('roles')).order_by('name'),
    })


def kiosk_login_id_code(request, kind, place_id):
    place = _place(kind, place_id)
    request.session.pop(KIOSK_USER, None)
    if request.method == 'POST':
        code = ''.join(ch for ch in request.POST.get('code', '') if ch.isdigit())
        profile = Profile.objects.filter(id_code=code, user__is_active=True).select_related('user').first() if len(code) == 6 else None
        if profile:
            return _sign_in(request, kind, place, profile.user)
        messages.error(request, _("We couldn't find that Volunteer ID. Please try again."))
    return render(request, 'jobs/kiosk_login_id_code.html', _place_context(kind, place))


def kiosk_login_email_password(request, kind, place_id):
    place = _place(kind, place_id)
    request.session.pop(KIOSK_USER, None)
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        match = User.objects.filter(email__iexact=email).first() if email else None
        user = authenticate(request, username=match.username if match else None, password=request.POST.get('password'))
        if user:
            return _sign_in(request, kind, place, user)
        messages.error(request, _('Invalid email or password.'))
    return render(request, 'jobs/kiosk_login_email_password.html', _place_context(kind, place))


def _eligible_event_slots(event, user):
    """Slots happening now (or within the hour) the user could cover on the spot."""
    now = timezone.now()
    slots = (event.role_slots.filter(start_time__lte=now + EARLY_CHECK_IN, end_time__gte=now)
             .select_related('role').annotate(n=Count('signups')).order_by('start_time'))
    return [
        s for s in slots
        if s.n < s.required_qty + s.allowed_overstaffing_qty and s.role.has_user_completed_required_training(user)
    ]


def kiosk_role_select(request, kind, place_id):
    """After sign-in: start the right shift, or let the volunteer pick a role."""
    place = _place(kind, place_id)
    user = _kiosk_user(request)
    if not user:
        return redirect('kiosk_login_id_code', kind=kind, place_id=place.pk)

    if Shift.objects.filter(user=user, end_time__isnull=True).exists():
        return redirect('kiosk_logged_in')

    now = timezone.now()
    if kind == 'event':
        mine = place.role_slots.filter(signups=user, start_time__lte=now + EARLY_CHECK_IN, end_time__gte=now).order_by('start_time')
        if mine.count() == 1 and request.method != 'POST':
            Shift.objects.create(user=user, event_role_slot=mine.first())
            return redirect('kiosk_logged_in')
        options = list(mine) or _eligible_event_slots(place, user)
        clutch = not mine.exists()

        if request.method == 'POST':
            slot = next((s for s in options if str(s.pk) == request.POST.get('choice')), None)
            if not slot:
                messages.error(request, _('Please choose one of the listed roles.'))
            else:
                if clutch:
                    slot.add_signup(user)
                Shift.objects.create(user=user, event_role_slot=slot, clutched=clutch)
                return redirect('kiosk_logged_in')

        upcoming = place.role_slots.filter(signups=user, start_time__gt=now + EARLY_CHECK_IN).order_by('start_time').first()
        choices = [{
            'id': s.pk, 'icon': s.role.icon, 'name': s.role.name, 'description': s.role.description,
            'detail': time_range(s.start_time, s.end_time),
        } for s in options]
    else:
        roles = list(user.profile.permanent_roles.filter(Q(venue=place) | Q(venue__isnull=True)).distinct())
        if request.method == 'POST':
            role = next((r for r in roles if str(r.pk) == request.POST.get('choice')), None)
            if not role:
                messages.error(request, _('Please choose one of the listed roles.'))
            else:
                Shift.objects.create(user=user, role=role)
                return redirect('kiosk_logged_in')
        clutch, upcoming = False, None
        choices = [{'id': r.pk, 'icon': r.icon, 'name': r.name, 'description': r.description, 'detail': ''} for r in roles]

    return render(request, 'jobs/kiosk_role_select.html', {
        **_place_context(kind, place),
        'kiosk_user': user,
        'choices': choices,
        'clutch': clutch,
        'upcoming': upcoming,
    })


def kiosk_logged_in(request):
    user = _kiosk_user(request)
    shift = Shift.objects.filter(user=user, end_time__isnull=True).select_related(
        'role', 'event_role_slot__event', 'event_role_slot__role').first() if user else None
    if not shift:
        messages.info(request, _('Your kiosk session has ended.'))
        return redirect('kiosk_home') if request.user.is_staff else redirect('home')

    slot = shift.event_role_slot
    context = {'kiosk_user': user, 'shift': shift, 'idle_timeout': SiteSettings.get_settings().kiosk_idle_timeout_seconds}
    if slot:
        event = slot.event
        team = slot.signups.all()
        arrived = Shift.objects.filter(event_role_slot=slot, end_time__isnull=True).values_list('user_id', flat=True)
        hours = max(1, (slot.end_time - slot.start_time).total_seconds() / 3600)
        context.update({
            'kind': 'event', 'place': event, 'slot': slot,
            'team_size': team.count(),
            'team_arrived': User.objects.filter(pk__in=arrived).select_related('profile'),
            'estimated_impact': round(hours * max(event.attendees, 1)),
            'supervisor': event.coordinators.select_related('profile').first(),
        })
    else:
        venue = shift.role.venue.first() if shift.role else None
        context.update({
            'kind': 'venue', 'place': venue,
            'estimated_impact': shift.role.regular_number_of_beneficiaries if shift.role else None,
        })
    return render(request, 'jobs/kiosk_logged_in.html', context)


@require_POST
def kiosk_check_out(request):
    user = _kiosk_user(request)
    shift = Shift.objects.filter(user=user, end_time__isnull=True).select_related('event_role_slot').first() if user else None
    if shift:
        shift.end_shift()
        hours = shift.duration()
        messages.success(request, _('Thanks, %(name)s! You logged %(hours)s hours and earned %(points)s impact points.') % {
            'name': user.first_name or user.username,
            'hours': round(hours, 1),
            'points': shift.end_impact_points or 0,
        })
    return kiosk_sign_out(request)


def kiosk_sign_out(request):
    """End the kiosk session (shift keeps running) and return to the ID screen."""
    user = _kiosk_user(request)
    request.session.pop(KIOSK_USER, None)
    kind = request.POST.get('kind') or request.GET.get('kind')
    place_id = request.POST.get('place') or request.GET.get('place')
    if kind in ('event', 'venue') and str(place_id).isdigit():
        return redirect('kiosk_login_id_code', kind=kind, place_id=place_id)
    shift = Shift.objects.filter(user=user).select_related('event_role_slot').order_by('-start_time').first() if user else None
    if shift and shift.event_role_slot_id:
        return redirect('kiosk_login_id_code', kind='event', place_id=shift.event_role_slot.event_id)
    if shift and shift.role and shift.role.venue.exists():
        return redirect('kiosk_login_id_code', kind='venue', place_id=shift.role.venue.first().pk)
    return redirect('home')
