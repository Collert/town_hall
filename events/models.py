from django.utils import timezone
from django.conf import settings
import uuid

from django.db import models

class Event(models.Model):
    title = models.CharField(max_length=100)
    description = models.TextField()
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    location = models.CharField(max_length=200)
    report_to_location = models.CharField(max_length=200, blank=True, null=True, help_text='Where volunteers should report to inside the location venue')
    latitude = models.FloatField(blank=True, null=True)
    longitude = models.FloatField(blank=True, null=True)
    post_event_statement = models.TextField(blank=True, null=True)
    coordinators = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='coordinated_events', blank=True)
    image = models.ImageField(upload_to='event_images/', blank=True, null=True)
    featured = models.BooleanField(default=False)
    category = models.ManyToManyField('EventCategory', related_name='events', blank=True)

    def __str__(self):
        return self.title
    
    def save(self, *args, **kwargs):
        # Auto-populate coordinates based on location
        if self.location and not self.latitude and not self.longitude:
            try:
                import urllib.request
                import urllib.parse
                import json
                
                query = urllib.parse.urlencode({'q': self.location, 'format': 'json', 'limit': 1})
                url = f"https://nominatim.openstreetmap.org/search?{query}"
                req = urllib.request.Request(url, headers={'User-Agent': 'TownHallApp'})
                
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = json.loads(response.read().decode())
                    if data:
                        self.latitude = float(data[0]['lat'])
                        self.longitude = float(data[0]['lon'])
            except Exception as e:
                pass # Fail silently if geocoding fails

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
        start = self.start_date.astimezone(timezone.utc).strftime(dtfmt)
        end = self.end_date.astimezone(timezone.utc).strftime(dtfmt)
        # Escape special characters per RFC 5545
        title = self.title.replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;').replace('\n', '\\n')
        desc = self.description.replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;').replace('\n', '\\n')
        location = self.location.replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;').replace('\n', '\\n')
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

    def staffing_progress(self):
        """Calculate staffing progress as a percentage."""
        total_required = sum(slot.required_qty for slot in self.role_slots.all())
        total_signed_up = sum(slot.signups.count() for slot in self.role_slots.all())
        if total_required == 0:
            return 100
        return int((total_signed_up / total_required) * 100)
    
class EventCategory(models.Model):
    name = models.CharField(max_length=50)

    def __str__(self):
        return self.name

class EventRoleSlot(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='role_slots')
    role = models.ForeignKey('jobs.Role', related_name='opportunities', on_delete=models.CASCADE)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    required_qty = models.PositiveIntegerField(default=1)
    allowed_overstaffing_qty = models.PositiveIntegerField(default=0)
    signups = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='commitments', blank=True)
    is_public = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.role.name} for {self.event.title} at {self.start_time.strftime('%Y-%m-%d %H:%M')}"

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

    def save(self, *args, **kwargs):
        # Ensure end_time is after start_time
        if self.end_time <= self.start_time:
            raise ValueError("End time must be after start time.")
        # Ensure there are enough slots left for a new sign up
        if self.pk and self.signups.count() >= (self.required_qty + self.allowed_overstaffing_qty):
            raise ValueError("No more slots available.")
        super().save(*args, **kwargs)
    
class Shift(models.Model):
    event_role_slot = models.ForeignKey(EventRoleSlot, on_delete=models.CASCADE, related_name='shifts', blank=True, null=True)
    role = models.ForeignKey('jobs.Role', on_delete=models.CASCADE, related_name='shifts', blank=True, null=True, help_text='Optional role reference for shifts that are not tied to a specific event role slot')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='shifts')
    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        if self.event_role_slot:
            return f"{self.user.username} - {self.event_role_slot.role.name} for {self.event_role_slot.event.title} at {self.start_time.strftime('%Y-%m-%d %H:%M')}"
        elif self.role:
            return f"{self.user.username} - {self.role.name} at {self.start_time.strftime('%Y-%m-%d %H:%M')}"
        else:
            return f"{self.user.username} - Shift at {self.start_time.strftime('%Y-%m-%d %H:%M')}"
    
    def duration(self):
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds() / 3600  # Return duration in hours
        return 0
    
    def duration_minutes(self):
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds() / 60  # Return duration in minutes
        return 0
    
class EventSlotInvite(models.Model):
    event_role_slot = models.ForeignKey(EventRoleSlot, on_delete=models.CASCADE, related_name='invites')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='event_invites')
    token = models.CharField(max_length=64, unique=True, default=uuid.uuid4().hex)
    accepted = models.BooleanField(default=False)
    sent_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Invite for {self.user.username if self.user else 'Unknown User'} to {self.event_role_slot.role.name} at {self.event_role_slot.event.title}"

    def event(self):
        return self.event_role_slot.event