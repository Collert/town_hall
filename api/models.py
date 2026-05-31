from django.utils import timezone
import uuid

from django.db import models

from events.models import Shift

class APIKey(models.Model):
    key = models.CharField(max_length=40, unique=True, default=uuid.uuid4().hex)
    name = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return self.name or self.key

class ShiftHeartbeat(models.Model):
    shift = models.ForeignKey('events.Shift', on_delete=models.CASCADE, related_name='heartbeats')
    latest_timestamp = models.DateTimeField(auto_now=True)
    role = models.ForeignKey('jobs.Role', on_delete=models.CASCADE, related_name='heartbeats')

    def save(self, user, role, *args, **kwargs):
        self.shift = Shift.objects.get_or_create(role=role, user=user, end_time=None)[0]  # Create a shift with the given user if it doesn't exist
        return super().save(*args, **kwargs)
    
    def die(self):
        if self.shift and not self.shift.end_time:
            self.shift.end_time = timezone.now()
            self.shift.save()
            self.delete()

    def __str__(self):
        return f"ShiftHeartbeat for {self.shift} at {self.latest_timestamp}"