from datetime import timezone as dt_timezone

from django.utils import timezone
from django.conf import settings
import uuid

from django.db import models
from django.db.models import Q
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver
import math

def generate_token():
    return uuid.uuid4().hex


def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) * math.sin(dlat / 2) + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(dlon / 2) * math.sin(dlon / 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

class Event(models.Model):
    title = models.CharField(max_length=100)
    description = models.TextField()
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    venue = models.ForeignKey('base.Venue', on_delete=models.SET_NULL, blank=True, null=True, related_name='events',
                              help_text='Where the event takes place. Leave blank and fill in location for a one-off address.')
    location = models.CharField(max_length=200, blank=True, default='', help_text='One-off address, used when no venue is chosen')
    report_to_location = models.CharField(max_length=200, blank=True, null=True, help_text='Where volunteers should report to inside the location venue')
    latitude = models.FloatField(blank=True, null=True)
    longitude = models.FloatField(blank=True, null=True)
    post_event_statement = models.TextField(blank=True, null=True)
    coordinators = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='coordinated_events', blank=True)
    image = models.ImageField(upload_to='event_images/', blank=True, null=True)
    featured = models.BooleanField(default=False)
    category = models.ManyToManyField('EventCategory', related_name='events', blank=True)
    attendees = models.IntegerField(default=0, help_text='Number of attendees (for calculating impact points)')
    published = models.BooleanField(default=True, help_text='Unpublished events are hidden from volunteers')
    # Chain of command (events/leadership.py): slots are grouped into areas, each area has
    # leads who report to the coordinators, and every slot has a shift lead.
    chain_of_command = models.BooleanField(default=False, help_text='Group roles into areas with area and shift leads')
    leads_auto_assigned_at = models.DateTimeField(blank=True, null=True, editable=False,
                                                  help_text='When open shift lead positions were filled automatically')

    def __str__(self):
        return self.title
    
    def save(self, *args, **kwargs):
        # Coordinates (for distance search) come from the venue, or are geocoded from a one-off address.
        if self.venue_id:
            self.latitude, self.longitude = self.venue.latitude, self.venue.longitude
        elif self.location and self.latitude is None and self.longitude is None:
            from base.geo import geocode
            self.latitude, self.longitude = geocode(self.location) or (None, None)

        # Delete all role invites after the event is over
        if self.pk and self.end_date < timezone.now():
            for role_slot in self.role_slots.all():
                role_slot.invites.all().delete()
        super().save(*args, **kwargs)

    def generate_ics(self):
        """Generate an iCalendar (.ics) file content for this event."""
        from django.utils.dateformat import format as date_format
        dtfmt = '%Y%m%dT%H%M%SZ'
        now = timezone.now().strftime(dtfmt)
        start = self.start_date.astimezone(dt_timezone.utc).strftime(dtfmt)
        end = self.end_date.astimezone(dt_timezone.utc).strftime(dtfmt)
        # Escape special characters per RFC 5545
        title = self.title.replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;').replace('\n', '\\n')
        desc = self.description.replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;').replace('\n', '\\n')
        location = self.full_location.replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;').replace('\n', '\\n')
        uid = f"event-{self.pk}@townhall"
        return (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//TownHall//Events//EN\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:{uid}\r\n"
            f"DTSTAMP:{now}\r\n"
            f"DTSTART:{start}\r\n"
            f"DTEND:{end}\r\n"
            f"SUMMARY:{title}\r\n"
            f"DESCRIPTION:{desc}\r\n"
            f"LOCATION:{location}\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )

    @property
    def place_name(self):
        """Short "where": the venue name, or the one-off address."""
        return self.venue.name if self.venue_id else self.location

    @property
    def address(self):
        """Street address: the venue's, or the one-off address."""
        return (self.venue.address or '') if self.venue_id else self.location

    @property
    def full_location(self):
        """One line for calendars and emails, e.g. "Central Library, 1 Main St"."""
        if not self.venue_id:
            return self.location
        return ', '.join(part for part in (self.venue.name, self.venue.address) if part)

    @property
    def map_url(self):
        from base.geo import map_url
        return map_url(self.latitude, self.longitude, self.address)

    def staffing_progress(self):
        """Calculate staffing progress as a percentage."""
        total_required = self.total_required()
        if total_required == 0:
            return 100
        return min(100, int((self.total_signed_up() / total_required) * 100))

    def total_required(self):
        return self.role_slots.aggregate(total=models.Sum('required_qty'))['total'] or 0

    def total_signed_up(self):
        return EventRoleSlot.signups.through.objects.filter(eventroleslot__event=self).count()

    def volunteers(self):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.filter(commitments__event=self).distinct()

    @property
    def is_live(self):
        now = timezone.now()
        return self.start_date <= now <= self.end_date

    @property
    def is_past(self):
        return self.end_date < timezone.now()

    @classmethod
    def search_events(cls, query, date_filter=None, category=None, user_lat=None, user_lon=None, distance=None):
        events = cls.objects.filter(end_date__gte=timezone.now(), published=True).select_related('venue').order_by('start_date')
    
        if query:
            events = events.filter(
                Q(title__icontains=query) |
                Q(description__icontains=query) |
                Q(location__icontains=query) |
                Q(venue__name__icontains=query) |
                Q(venue__address__icontains=query) |
                Q(category__name__icontains=query)
            ).distinct()

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

        if category:
            events = events.filter(category__name__iexact=category)

        if user_lat and user_lon and distance and distance != '101':  # '101' is the code for "Any distance"
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

        return events
    
    def shifts(self):
        from jobs.models import Shift
        return Shift.objects.filter(event_role_slot__event=self)

    @property
    def hours_logged(self):
        """Hours worked across all shifts, counting open shifts up to now."""
        now = timezone.now()
        total_seconds = sum(
            ((shift.end_time or now) - shift.start_time).total_seconds()
            for shift in self.shifts()
        )
        return round(total_seconds / 3600)
    
class EventCategory(models.Model):
    name = models.CharField(max_length=50)

    def __str__(self):
        return self.name

class EventArea(models.Model):
    """A physical place or area of responsibility at an event with a chain of command
    (kitchen, stage, cleaning...). It groups role slots. Its area leads are the people
    signed up for its slots in the built-in "Area lead" role (``lead_slots``)."""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='areas')
    name = models.CharField(max_length=100)
    icon = models.CharField(max_length=50, blank=True, default='', help_text='Icon name from Google Material Symbols')
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'pk']

    def __str__(self):
        return f'{self.name} ({self.event})'

    def work_slots(self):
        """The area's volunteer shifts (everything but its area lead shifts)."""
        return self.slots.exclude(role__system_key='area_lead')

    def lead_slots(self):
        """The area's area lead shifts, earliest first, with the lead on each."""
        return (self.slots.filter(role__system_key='area_lead').order_by('start_time', 'pk')
                .prefetch_related('signups__profile'))

    def window(self):
        """(start, end) the area is active: its earliest shift start to its latest shift
        end, or the whole event while it has no shifts."""
        bounds = self.work_slots().aggregate(start=models.Min('start_time'), end=models.Max('end_time'))
        if bounds['start'] is None:
            return self.event.start_date, self.event.end_date
        return bounds['start'], bounds['end']


class EventRoleSlot(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='role_slots')
    role = models.ForeignKey('jobs.Role', related_name='opportunities', on_delete=models.CASCADE)
    area = models.ForeignKey(EventArea, on_delete=models.SET_NULL, blank=True, null=True, related_name='slots')
    # One of the slot's sign-ups; picked by a coordinator or automatically (events/leadership.py).
    lead = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name='led_slots')
    lead_auto_assigned = models.BooleanField(default=False)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    required_qty = models.PositiveIntegerField(default=1)
    allowed_overstaffing_qty = models.PositiveIntegerField(default=0)
    signups = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='commitments', blank=True)
    is_public = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.role.name} for {self.event.title} at {self.start_time.strftime('%Y-%m-%d %H:%M')}"

    @property
    def is_area_lead(self):
        """An area lead's shift (the built-in "Area lead" role), not a volunteer shift."""
        return self.role.system_key == 'area_lead'

    @property
    def person(self):
        """Whoever holds a one-person slot such as an area lead shift (uses prefetched signups)."""
        return next(iter(self.signups.all()), None)

    def is_fully_staffed(self):
        return self.signups.count() >= self.required_qty
    
    def is_overstaffed(self):
        return self.signups.count() > (self.required_qty + self.allowed_overstaffing_qty)

    def available_slots(self):
        return max(0, self.required_qty - self.signups.count())
    
    def overstaffing_slots_left(self):
        return max(0, (self.required_qty + self.allowed_overstaffing_qty) - self.signups.count())

    def user_has_conflict(self, user):
        user_slots = EventRoleSlot.objects.filter(signups=user)
        for slot in user_slots:
            if (self.start_time < slot.end_time and self.end_time > slot.start_time):
                return True
        return False

    def user_signed_up(self, user):
        return self.signups.filter(pk=user.pk).exists()

    def add_signup(self, user):
        """Sign `user` up and remember when, which decides the last-minute points bonus."""
        from base.models import PointsRules
        window = timezone.timedelta(hours=PointsRules.get().last_minute_window_hours)
        last_minute = (timezone.now() >= self.start_time - window and self.end_time > timezone.now()
                       and self.signups.count() < self.required_qty)
        self.signups.add(user)
        SlotSignup.objects.get_or_create(slot=self, user=user, defaults={'last_minute': last_minute})

    def save(self, *args, **kwargs):
        # Ensure end_time is after start_time
        if self.end_time <= self.start_time:
            raise ValueError("End time must be after start time.")
        super().save(*args, **kwargs)
    
class SlotSignup(models.Model):
    """When someone signed up for a slot. `last_minute` marks filling an understaffed slot
    shortly before it started."""
    slot = models.ForeignKey(EventRoleSlot, on_delete=models.CASCADE, related_name='signup_log')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='slot_signups')
    created_at = models.DateTimeField(auto_now_add=True)
    last_minute = models.BooleanField(default=False)

    class Meta:
        unique_together = ('slot', 'user')

    def __str__(self):
        return f"{self.user} signed up for {self.slot}"


class EventSlotInvite(models.Model):
    event_role_slot = models.ForeignKey(EventRoleSlot, on_delete=models.CASCADE, related_name='invites')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='event_invites')
    token = models.CharField(max_length=64, unique=True, default=generate_token)
    accepted = models.BooleanField(default=False)
    sent_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Invite for {self.user.username if self.user else 'Unknown User'} to {self.event_role_slot.role.name} at {self.event_role_slot.event.title}"

    def event(self):
        return self.event_role_slot.event


class EventTaskList(models.Model):
    """A Bell Tower task list attached to an event (base/belltower.py holds the tasks).

    Each event gets one planning list (prep work for organizers) and any number of
    lists for the day itself (volunteers, staff, pager devices). `belltower_url` records
    which server the list lives on, so reconnecting to another server hides old lists.
    """
    PLANNING = 'planning'
    EVENT_DAY = 'event'
    KIND_CHOICES = [(PLANNING, 'Planning'), (EVENT_DAY, 'During the event')]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='task_lists')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=EVENT_DAY)
    # The roles whose sign-ups join this list (events/belltower_sync.py). Each role at the
    # event starts with its own "<Role> tasks" list; merging lists combines their roles.
    roles = models.ManyToManyField('jobs.Role', blank=True, related_name='event_task_lists')
    # With a chain of command, role lists are per area too, so kitchen cleaners and kids
    # zone cleaners get separate lists. Only lists of the same area merge.
    area = models.ForeignKey('EventArea', on_delete=models.SET_NULL, blank=True, null=True, related_name='task_lists')
    name = models.CharField(max_length=200)
    belltower_url = models.URLField()
    belltower_id = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [
            models.UniqueConstraint(fields=['belltower_url', 'belltower_id'], name='unique_belltower_list'),
        ]

    def __str__(self):
        return f'{self.name} ({self.event})'

    @property
    def role_icon(self):
        """The icon shown on a role list's card (its first role's); '' for other lists."""
        role = next(iter(self.roles.all()), None)
        return (role.icon or 'badge') if role else ''


class PlanningChat(models.Model):
    """A staff member's conversation with the AI planning assistant about one event
    (console/planner.py). ``messages`` is the Claude API transcript, kept as sent so it
    can be replayed; ``busy`` is set while the assistant works in the background."""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='planning_chats')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='planning_chats')
    messages = models.JSONField(default=list, blank=True)
    busy = models.BooleanField(default=False)
    error = models.TextField(blank=True, default='')
    # Bumped whenever a tool changes the event, so the open page can offer a reload.
    changes = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['event', 'user'], name='unique_planning_chat')]

    def __str__(self):
        return f'Planning chat: {self.event} / {self.user}'


class EventFeedback(models.Model):
    """A volunteer's post-event survey response."""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='feedback')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='event_feedback')
    rating = models.PositiveSmallIntegerField(choices=[(i, str(i)) for i in range(1, 6)])
    enjoyed = models.TextField(blank=True, default='')
    suggestions = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('event', 'user')

    def __str__(self):
        return f"{self.user.username} rated {self.event.title} {self.rating}/5"


# Bell Tower: role lists and their members follow slots and sign-ups (events/belltower_sync.py).
# The work runs after the transaction commits, in the background.

@receiver(post_save, sender=EventRoleSlot)
def queue_role_list(sender, instance, created, **kwargs):
    from base import belltower
    if created and belltower.is_connected():
        from . import belltower_sync
        belltower.run_after_commit(belltower_sync.role_slot_added, instance.event_id, instance.role_id, instance.area_id)


@receiver(m2m_changed, sender=EventRoleSlot.signups.through)
def queue_role_list_members(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ('post_add', 'post_remove') or not pk_set:
        return
    if action == 'post_remove':  # someone who leaves a slot stops leading it
        from .leadership import drop_lead_if_gone
        slot_ids, user_ids = (list(pk_set), [instance.pk]) if reverse else ([instance.pk], list(pk_set))
        drop_lead_if_gone(slot_ids, user_ids)
    from base import belltower
    if not belltower.is_connected():
        return
    from . import belltower_sync
    # slot.signups.add(user) or user.commitments.add(slot): either side can be the instance.
    slot_ids, user_ids = (list(pk_set), [instance.pk]) if reverse else ([instance.pk], list(pk_set))
    belltower.run_after_commit(belltower_sync.signups_changed, slot_ids, user_ids, action == 'post_add')
