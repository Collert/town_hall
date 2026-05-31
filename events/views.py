from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone
from django.db.models import Sum, F, Q
from .models import Event, EventRoleSlot, Shift
from education.models import TrainingModule, TrainingModuleCompletion
from jobs.models import RoleTrainingRequirement
import math


def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) * math.sin(dlat / 2) + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(dlon / 2) * math.sin(dlon / 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def explore_events(request):
    events = Event.objects.filter(end_date__gte=timezone.now()).order_by('start_date')
    
    query = request.GET.get('q')
    if query:
        events = events.filter(
            Q(title__icontains=query) |
            Q(description__icontains=query) |
            Q(location__icontains=query) |
            Q(category__name__icontains=query)
        ).distinct()

    date_filter = request.GET.get('date')
    if date_filter == 'today':
        now = timezone.now()
        events = events.filter(start_date__date=now.date())
    elif date_filter == 'this_week':
        now = timezone.now()
        end_of_week = now + timezone.timedelta(days=7 - now.weekday())
        events = events.filter(start_date__gte=now, start_date__lte=end_of_week)
    elif date_filter == 'this_month':
        now = timezone.now()
        events = events.filter(start_date__year=now.year, start_date__month=now.month)

    category = request.GET.get('category')
    if category:
        events = events.filter(category__name__iexact=category)

    user_lat = request.GET.get('lat')
    user_lon = request.GET.get('lng')
    distance = request.GET.get('distance')
    
    if user_lat and user_lon and distance:
        try:
            user_lat = float(user_lat)
            user_lon = float(user_lon)
            max_dist = float(distance)
            
            # Rough bounding box filter to speed up queries if large
            lon_delta = max_dist / (111.32 * math.cos(math.radians(user_lat)))
            lat_delta = max_dist / 111.32
            
            events = events.filter(
                latitude__gte=user_lat - lat_delta,
                latitude__lte=user_lat + lat_delta,
                longitude__gte=user_lon - lon_delta,
                longitude__lte=user_lon + lon_delta,
            )
            
            # Exact haversine filter
            valid_events_pks = []
            for event in events:
                if event.latitude is not None and event.longitude is not None:
                    dist = haversine_distance(user_lat, user_lon, event.latitude, event.longitude)
                    if dist <= max_dist:
                        valid_events_pks.append(event.pk)
            
            events = events.filter(pk__in=valid_events_pks)
        except ValueError:
            pass
            
    total_open_roles = 0
    for slot in EventRoleSlot.objects.filter(event__in=events, event__end_date__gte=timezone.now()):
        total_open_roles += slot.available_slots()
        
    # Get available categories for filter dropdown
    from .models import EventCategory
    categories = EventCategory.objects.all()

    # Calculate trending events based on views
    base_upcoming = Event.objects.filter(end_date__gte=timezone.now())
    trending_events_list = []
    for e in base_upcoming:
        cache_key = f'event_view_count_{e.pk}'
        # Count could be None or int
        views_count = cache.get(cache_key) or 0
        trending_events_list.append((e, views_count))
    
    trending_events_list.sort(key=lambda x: x[1], reverse=True)
    trending_events = [item[0] for item in trending_events_list[:2]]

    return render(request, 'events/explore_events.html', {
        'events': events,
        'total_open_roles': total_open_roles,
        'categories': categories,
        'trending_events': trending_events,
    })


def event_detail(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    
    # Increment view count in cache
    cache_key = f'event_view_count_{event.pk}'
    try:
        cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, timeout=60*60*3) # Cache for 3 hours
        
    all_role_slots = event.role_slots.prefetch_related('role').all()
    
    unique_role_slots = []
    seen_roles = {}
    
    for slot in all_role_slots:
        role_ids = (slot.role.id,)
        if role_ids not in seen_roles:
            user_signed_up_any = slot.user_signed_up(request.user) if request.user.is_authenticated else False
            seen_roles[role_ids] = {
                'slot': slot,
                'count': 1,
                'total_available': slot.available_slots(),
                'all_fully_staffed': slot.is_fully_staffed(),
                'user_signed_up_any': user_signed_up_any,
            }
            slot.user_signed_up_any = user_signed_up_any
            slot.total_available_spots = slot.available_slots()
            slot.multiple_slots = False
            slot.all_fully_staffed = slot.is_fully_staffed()
            unique_role_slots.append(slot)
        else:
            seen = seen_roles[role_ids]
            seen['count'] += 1
            seen['total_available'] += slot.available_slots()
            seen['all_fully_staffed'] = seen['all_fully_staffed'] and slot.is_fully_staffed()
            if request.user.is_authenticated and not seen['user_signed_up_any'] and slot.user_signed_up(request.user):
                seen['user_signed_up_any'] = True
            
            # Update the original slot reference
            first_slot = seen['slot']
            first_slot.user_signed_up_any = seen['user_signed_up_any']
            first_slot.total_available_spots = seen['total_available']
            first_slot.multiple_slots = True
            first_slot.all_fully_staffed = seen['all_fully_staffed']
            
    role_slots = unique_role_slots
    
    role_slots_data = {}
    for slot in all_role_slots:
        roles = [slot.role]
        other_slots = EventRoleSlot.objects.filter(event=slot.event, role__in=roles).order_by('start_time').distinct()
        
        timeslot_data = []
        for s in other_slots:
            user_signed_up = s.user_signed_up(request.user) if request.user.is_authenticated else False
            timeslot_data.append({
                'id': s.pk,
                'start_time': s.start_time,
                'end_time': s.end_time,
                'available': s.available_slots(),
                'is_full': s.is_fully_staffed(),
                'is_selected': (s.pk == slot.pk),
                'user_signed_up': user_signed_up,
            })
            
        slot_training_modules = TrainingModule.objects.filter(roles__in=roles).distinct()
        completed_modules = TrainingModuleCompletion.objects.filter(user=request.user).values_list('training_module', flat=True) if request.user.is_authenticated else []
        
        training_data = []
        all_completed = True
        
        # Get mandatory requirements for these roles
        mandatory_module_ids = set(RoleTrainingRequirement.objects.filter(
            role__in=roles, 
            mandatory=True
        ).values_list('training_module_id', flat=True))

        for mod in slot_training_modules:
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
            'role_names': ", ".join(r.name for r in roles),
            'role_icons': [r.icon for r in roles if r.icon],
            'role_desc': " / ".join(r.description for r in roles if r.description),
            'slots': timeslot_data,
            'training': training_data,
            'all_completed': all_completed,
            'user_can_signup': request.user.is_authenticated and all_completed and not slot.is_fully_staffed() and not slot.user_has_conflict(request.user),
        }

    # Get distinct volunteers
    from django.contrib.auth import get_user_model
    User = get_user_model()
    event_volunteers = User.objects.filter(commitments__event=event).distinct()

    return render(request, 'events/event_detail.html', {
        'event': event,
        'role_slots': role_slots,
        'role_slots_data': role_slots_data,
        'event_volunteers': event_volunteers,
    })

def role_slot_signup(request, slot_id):
    if request.method != 'POST':
        messages.error(request, "Invalid method.")
        return redirect(request.META.get('HTTP_REFERER', 'explore_opportunities'))
        
    if not request.user.is_authenticated:
        messages.error(request, "Please log in to sign up.")
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))
        
    slot = get_object_or_404(EventRoleSlot, pk=slot_id)
    
    # Check training
    roles = [slot.role]
    for role in roles:
        if not role.has_user_completed_required_training(request.user):
            messages.error(request, f"You haven't completed the required training modules for {role.name}.")
            return redirect('opportunity_detail', event_id=slot.event.id)
            
    if slot.is_fully_staffed():
        messages.error(request, "Sorry, this slot just filled up!")
        return redirect('opportunity_detail', event_id=slot.event.id)

    if slot.user_has_conflict(request.user):
        messages.error(request, "You have other commitments that conflict with this slot.")
        return redirect('opportunity_detail', event_id=slot.event.id)
        
    slot.signups.add(request.user)
    messages.success(request, f"Successfully signed up for {slot.role.name} at {slot.event.title}!")
    return redirect('opportunity_detail', event_id=slot.event.id)


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
        .select_related('event', 'role')
        .order_by('event__start_date', 'start_time')
    )

    # Group upcoming slots by event, keeping earliest slot per event for display
    upcoming_events_map = {}
    for slot in upcoming_slots:
        eid = slot.event.pk
        if eid not in upcoming_events_map:
            slot.event.is_today = slot.event.start_date.date() == now.date()
            upcoming_events_map[eid] = {'event': slot.event, 'slot': slot}

    upcoming_events = list(upcoming_events_map.values())

    # "Today" badge count — slots starting today
    today_shifts_count = sum(
        1 for item in upcoming_events if item['event'].start_date.date() == now.date()
    )

    # Past: slots the user signed up for where the event has already ended
    past_slots = (
        EventRoleSlot.objects
        .filter(signups=user, event__end_date__lt=now)
        .select_related('event', 'role')
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

    # Stats
    total_available = Event.objects.filter(end_date__gte=now).count()
    profile = getattr(user, 'profile', None)
    impact_points = profile.impact_points if profile else 0

    return render(request, 'events/my_events.html', {
        'upcoming_events': upcoming_events,
        'past_events': past_events_data,
        'today_shifts_count': today_shifts_count,
        'total_available': total_available,
        'upcoming_count': len(upcoming_events),
        'impact_points': impact_points,
        'now': now,
    })
