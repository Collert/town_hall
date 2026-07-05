from statistics import mean

from django.db import models
from django.conf import settings
from education.models import TrainingModule, TrainingModuleCompletion
from django.utils import timezone

class Role(models.Model):
    name = models.CharField(max_length=50)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, help_text='Icon name from Google Material Icons')
    permanent = models.BooleanField(default=False, help_text='Whether this role is a permanent position (not tied to specific events)')
    regular_number_of_beneficiaries = models.PositiveIntegerField(help_text='Typical number of beneficiaries served by this role per hour (used for impact points calculation for permanent roles)', blank=True, null=True)
    venue = models.ManyToManyField('base.Venue', related_name='roles', blank=True, help_text='Venues where this role is applicable. Leave blank if the role is not venue-specific.')
    training_modules = models.ManyToManyField(TrainingModule, through='RoleTrainingRequirement', related_name='roles', blank=True)

    def __str__(self):
        return self.name
    
    @property
    def complexity_level(self):
        role_total_length = sum(module.get_total_length() for module in self.training_modules.all())
        if role_total_length == 0:
            return 0  # Default complexity level if no modules are associated

        role_lengths = [
            sum(module.get_total_length() for module in role.training_modules.all())
            for role in Role.objects.prefetch_related('training_modules').all()
        ]
        floor = min(role_lengths, default=0)
        ceiling = max(role_lengths, default=0)

        # Edge case: all roles have the same total length.
        if ceiling == floor:
            return 2

        percentage = ((role_total_length - floor) / (ceiling - floor)) * 100

        if percentage <= 20:
            return 0
        elif 20 < percentage < 40:
            return 1
        elif 40 <= percentage < 60:
            return 2
        elif 60 <= percentage < 80:
            return 3
        elif percentage >= 80:
            return 4
        return 1
        

    def has_user_completed_required_training(self, user):
        if not user.is_authenticated:
            return False
        required_modules = TrainingModule.objects.filter(
            roletrainingrequirement__role=self,
            roletrainingrequirement__mandatory=True
        ).distinct()
        completed_modules = TrainingModuleCompletion.objects.filter(user=user).values_list('training_module', flat=True)
        for mod in required_modules:
            if mod.id not in completed_modules:
                return False
        return True

class RoleTrainingRequirement(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    training_module = models.ForeignKey(TrainingModule, on_delete=models.CASCADE)
    mandatory = models.BooleanField(default=False)

    class Meta:
        unique_together = ('role', 'training_module')

    def __str__(self):
        return f"{self.training_module.title} for {self.role.name} {'(Mandatory)' if self.mandatory else ''}"

class Shift(models.Model):
    event_role_slot = models.ForeignKey('events.EventRoleSlot', on_delete=models.CASCADE, related_name='shifts', blank=True, null=True)
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='shifts', blank=True, null=True, help_text='Optional role reference for shifts that are not tied to a specific event role slot')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='shifts')
    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True, blank=True)
    clutched = models.BooleanField(default=False, help_text='Whether this shift was clutched (i.e., the user stepped in to cover a shift at the last minute)')
    end_impact_points = models.IntegerField(null=True, blank=True, help_text='Impact points calculated at the end of the shift')
    
    def __str__(self):
        if self.event_role_slot:
            return f"{self.user.username} - {self.event_role_slot.role.name} for {self.event_role_slot.event.title} at {self.start_time.strftime('%Y-%m-%d %H:%M')}"
        elif self.role:
            return f"{self.user.username} - {self.role.name} at {self.start_time.strftime('%Y-%m-%d %H:%M')}"
        else:
            return f"{self.user.username} - Shift at {self.start_time.strftime('%Y-%m-%d %H:%M')}"

    def duration(self):
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds() / 3600
        return 0

    def duration_minutes(self):
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds() / 60
        return 0

    def calculate_impact_points(self):
        """Calculate impact points based on the role and duration of the shift."""
        hours = self.duration()
        role_complexity_multiplier = 1 + (self.role.complexity_level / 10) if self.role else 1
        clutch_multiplier = 1.5 if self.clutched else 1
        if self.role and self.role.permanent and self.role.regular_number_of_beneficiaries:
            # For permanent roles, calculate points based on beneficiaries served
            return int(hours * self.role.regular_number_of_beneficiaries * role_complexity_multiplier * clutch_multiplier)
        else:
            points = int(hours) * \
            (self.event_role_slot.event.attendees if self.event_role_slot and self.event_role_slot.event else 0) * \
            role_complexity_multiplier * clutch_multiplier
            return points
        
    def end_shift(self):
        """End the shift by setting the end_time and calculating impact points."""
        if not self.end_time:
            self.end_time = timezone.now()
            self.end_impact_points = self.calculate_impact_points()
            self.save()

    def save(self, *args, **kwargs):
        if self.end_time and self.end_time < self.start_time:
            raise ValueError("End time cannot be before start time.")
        self.end_impact_points = self.calculate_impact_points() if self.end_time else None
        super().save(*args, **kwargs)
        if self.event_role_slot and not self.role:
            self.user.profile.impact_points = sum(shift.end_impact_points for shift in self.user.shifts.filter(end_time__isnull=False) if shift.end_impact_points is not None)
            self.user.profile.save()
            self.role = self.event_role_slot.role
            super().save(update_fields=['role'])