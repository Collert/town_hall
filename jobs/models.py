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
    venue = models.ManyToManyField('base.Venue', related_name='roles', blank=True, help_text='Venues where this role is applicable. Leave blank if the role is not venue-specific. Used mainly for permanent roles.')
    training_modules = models.ManyToManyField(TrainingModule, through='RoleTrainingRequirement', related_name='roles', blank=True)
    points_weight = models.DecimalField(max_digits=3, decimal_places=1, blank=True, null=True,
                                        help_text='Impact points multiplier. Leave empty to set it from training length.')
    preferred_skills = models.ManyToManyField('education.Skill', related_name='preferred_for_roles', blank=True, help_text='Non-mandatory skills that improve volunteer matching')

    def __str__(self):
        return self.name
    
    @property
    def complexity_level(self):
        return Role.complexity_levels().get(self.pk, 0)

    def training_minutes(self):
        return sum(module.get_total_length() for module in self.training_modules.all())

    @classmethod
    def complexity_levels(cls):
        """{role_id: 0-4} relative to the training length of every other role.

        Computed for all roles at once so list views don't repeat the work per role.
        """
        roles = cls.objects.prefetch_related(
            'training_modules__lessons', 'training_modules__quizzes__questions',
        )
        lengths = {role.pk: role.training_minutes() for role in roles}
        floor = min(lengths.values(), default=0)
        ceiling = max(lengths.values(), default=0)

        levels = {}
        for role_id, length in lengths.items():
            if length == 0:
                levels[role_id] = 0  # No training modules associated
            elif ceiling == floor:
                levels[role_id] = 2  # Edge case: all roles have the same total length
            else:
                percentage = ((length - floor) / (ceiling - floor)) * 100
                if percentage <= 20:
                    levels[role_id] = 0
                elif percentage < 40:
                    levels[role_id] = 1
                elif percentage < 60:
                    levels[role_id] = 2
                elif percentage < 80:
                    levels[role_id] = 3
                else:
                    levels[role_id] = 4
        return levels
        

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

    def is_assigned_to(self, user):
        """Permanent roles can only be worked by people staff assigned them to (Profile.permanent_roles)."""
        if not self.permanent:
            return True
        return user.is_authenticated and user.profile.permanent_roles.filter(pk=self.pk).exists()

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
    start_time = models.DateTimeField(auto_now_add=True, editable=False)
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
        """Points for this shift under the current rules (see base.points)."""
        from base.points import shift_points
        return shift_points(self)[0]

    def end_shift(self):
        """End the shift by setting the end_time and calculating impact points."""
        if not self.end_time:
            self.end_time = timezone.now()
            self.save()

    def save(self, *args, **kwargs):
        if self.event_role_slot_id and not self.role_id:
            self.role = self.event_role_slot.role
        if self.start_time and self.end_time and self.end_time < self.start_time:
            raise ValueError("End time cannot be before start time.")
        super().save(*args, **kwargs)
        # A finished shift has one ledger entry, refreshed whenever its times are edited
        from base.points import award_shift
        award_shift(self)
