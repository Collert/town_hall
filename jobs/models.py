from django.db import models
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