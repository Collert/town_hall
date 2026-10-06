from django.utils import timezone
import uuid

from django.db import models

from jobs.models import Shift


def generate_key():
    return uuid.uuid4().hex


class APIKey(models.Model):
    key = models.CharField(max_length=40, unique=True, default=generate_key)
    name = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return self.name or self.key

class ShiftHeartbeat(models.Model):
    shift = models.ForeignKey('jobs.Shift', on_delete=models.CASCADE, related_name='heartbeats')
    latest_timestamp = models.DateTimeField(auto_now=True)
    role = models.ForeignKey('jobs.Role', on_delete=models.CASCADE, related_name='heartbeats')

    def save(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        role = kwargs.pop('role', None)
        if not self.shift_id:
            if user is None or role is None:
                raise ValueError('user and role are required to create a ShiftHeartbeat')
            self.shift, _ = Shift.objects.get_or_create(
                user=user,
                role=role,
                end_time__isnull=True,
                defaults={'role': role},
            )
        return super().save(*args, **kwargs)
    
    def die(self):
        if self.shift and not self.shift.end_time:
            self.shift.end_time = timezone.now()
            self.shift.save()
            self.delete()

    def __str__(self):
        return f"ShiftHeartbeat for {self.shift} at {self.latest_timestamp}"