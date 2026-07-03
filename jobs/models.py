from django.db import models
from django.conf import settings
from education.models import TrainingModule, TrainingModuleCompletion

class Role(models.Model):
    name = models.CharField(max_length=50)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, help_text='Icon name from Google Material Icons')
    permanent = models.BooleanField(default=False, help_text='Whether this role is a permanent position (not tied to specific events)')
    regular_number_of_beneficiaries = models.PositiveIntegerField(help_text='Typical number of beneficiaries served by this role per shift (used for impact points calculation for permanent roles)', blank=True, null=True)
    training_modules = models.ManyToManyField(TrainingModule, through='RoleTrainingRequirement', related_name='roles', blank=True)

    def __str__(self):
        return self.name

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

    def save(self, *args, **kwargs):
        if self.end_time and self.end_time < self.start_time:
            raise ValueError("End time cannot be before start time.")
        super().save(*args, **kwargs)
        if self.event_role_slot and not self.role:
            self.role = self.event_role_slot.role
            super().save(update_fields=['role'])