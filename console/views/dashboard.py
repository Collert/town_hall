from datetime import timedelta

from django.contrib.auth.models import User
from django.shortcuts import render
from django.utils import timezone

from events.models import Event
from education.models import UserCertification

from ..decorators import staff_required
from ..utils import activity_feed, hours_between, unfilled_slot_count, week_bounds


@staff_required
def dashboard(request):
    now = timezone.now()
    upcoming = Event.objects.filter(end_date__gte=now).select_related('venue').order_by('start_date')
    volunteers = User.objects.filter(is_active=True, is_staff=False)
    week_start, week_end = week_bounds()

    hour = timezone.localtime(now).hour
    greeting = 'morning' if hour < 12 else 'afternoon' if hour < 18 else 'evening'

    today = timezone.localdate()
    return render(request, 'console/dashboard.html', {
        'greeting': greeting,
        'active_volunteers': volunteers.count(),
        'new_volunteers': volunteers.filter(date_joined__gte=now - timedelta(days=30)).count(),
        'upcoming_count': upcoming.count(),
        'unfilled_roles': unfilled_slot_count(upcoming),
        'weekly_hours': hours_between(week_start, week_end),
        'events_today': upcoming.filter(start_date__date__lte=today, end_date__date__gte=today).count(),
        'pending_verifications': UserCertification.objects.filter(
            verified=False, rejected=False, files__isnull=False).distinct().count(),
        'upcoming_events': upcoming.prefetch_related('role_slots')[:4],
        'activity': activity_feed(limit=6),
    })
