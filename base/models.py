from django.db import models
from django.core.cache import cache
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    message = models.CharField(max_length=255)
    link = models.URLField(blank=True, null=True)
    read = models.BooleanField(default=False)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Notification for {self.user.username}: {self.message[:20]}..."

    def cleanup(self):
        """Delete notifications older than 30 days."""
        from django.utils import timezone
        cutoff = timezone.now() - timezone.timedelta(days=30)
        Notification.objects.filter(user=self.user, timestamp__lt=cutoff, read=True).delete()

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    last_viewed_training_module = models.ForeignKey('education.TrainingModule', on_delete=models.SET_NULL, blank=True, null=True, related_name='viewed_by_profiles')
    impact_points = models.IntegerField(default=0)
    permanent_roles = models.ManyToManyField('jobs.Role', blank=True, related_name='staff')
    bio = models.TextField(blank=True, default='')
    phone = models.CharField(max_length=30, blank=True, default='')
    location = models.CharField(max_length=100, blank=True, default='')
    skills = models.ManyToManyField('education.Skill', blank=True, related_name='user_profiles')

    def level(self):
        """Determine the user's level based on impact points."""
        return Level.objects.filter(min_points__lte=self.impact_points).last()

    def __str__(self):
        return f"{self.user.username} Profile"

class Endorsement(models.Model):
    endorser = models.ForeignKey(User, on_delete=models.CASCADE, related_name='given_endorsements')
    endorsed = models.ForeignKey(User, on_delete=models.CASCADE, related_name='endorsements')
    skill = models.ForeignKey('education.Skill', on_delete=models.CASCADE, related_name='endorsements')
    timestamp = models.DateTimeField(auto_now_add=True)
    text = models.TextField(blank=True, default='')

    class Meta:
        unique_together = ('endorser', 'endorsed', 'skill')

    def __str__(self):
        return f"{self.endorser.username} endorsed {self.endorsed.username} for {self.skill.name}"

@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)

class Level(models.Model):
    name = models.CharField(max_length=20)
    numeric_name = models.PositiveIntegerField(help_text='Numeric representation of the level for ordering (e.g., Level 1, Level 2)', unique=True)
    min_points = models.IntegerField()
    benefits = models.TextField(blank=True, null=True, help_text='Description of benefits for this level as a comma-separated list.')

    def __str__(self):
        return self.name

class HeroSection(models.Model):
    """Model for the homepage hero section content."""
    title = models.CharField(max_length=50, default='Welcome to Our Town Hall')
    subtitle = models.TextField(max_length=150 ,default='Engage with your community and stay informed about local news and events.')
    image = models.ImageField(upload_to='hero_images/', blank=True, null=True)
    button_1_text = models.CharField(max_length=20, default='Learn More')
    button_1_url = models.URLField(default='#')
    button_2_text = models.CharField(max_length=20, default='Get Involved')
    button_2_url = models.URLField(default='#')

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        # Ensure only one instance exists (singleton pattern)
        self.pk = 1
        super().save(*args, **kwargs)


class SiteSettings(models.Model):
    """
    Singleton model for site-wide settings including theme colors.
    Only one instance should exist - enforced by the save method.
    """

    company_name = models.CharField(
        max_length=30, default='Company Name',
        help_text='Name of the company or organization'
    )

    careers_page_url = models.URLField(
        default='https://www.example.com/careers',
        help_text='URL for the careers page'
    )

    max_skills_per_user = models.PositiveSmallIntegerField(
        default=10,
        help_text='Maximum number of skills a user can add to their profile'
    )

    # Primary Colors
    color_primary = models.CharField(
        max_length=7, default='#00434d',
        help_text='Main brand color'
    )
    color_primary_contrast = models.CharField(
        max_length=7, default='#ffffff',
        help_text='Color that contrasts well with the primary color for text and icons'
    )
    color_primary_dark_offset = models.PositiveSmallIntegerField(
        default=20,
        help_text='Darker variant offset for hover states'
    )
    color_primary_light_offset = models.PositiveSmallIntegerField(
        default=40,
        help_text='Lighter variant offset for hover states'
    )
    
    # Accent Colors
    color_accent = models.CharField(
        max_length=7, default='#ac3509',
        help_text='Call-to-action buttons and highlights'
    )
    color_accent_contrast = models.CharField(
        max_length=7, default='#ffffff',
        help_text='Color that contrasts well with the primary accent color for text and icons'
    )
    color_accent_dark_offset = models.PositiveSmallIntegerField(
        default=20,
        help_text='Darker variant offset for hover states'
    )
    color_accent_light_offset = models.PositiveSmallIntegerField(
        default=40,
        help_text='Lighter variant offset for hover states'
    )
    
    # Background Colors
    color_bg_primary = models.CharField(
        max_length=7, default='#ebfdfc',
        help_text='Main page background'
    )
    color_bg_secondary = models.CharField(
        max_length=7, default='#dff1f0',
        help_text='Secondary/alternate background'
    )
    color_bg_tertiary = models.CharField(
        max_length=7, default='#d7e7e6',
        help_text='Elevated surfaces (cards, modals)'
    )
    
    # Text Colors
    color_text_primary = models.CharField(
        max_length=7, default='#0e1e1e',
        help_text='Main body text'
    )
    color_text_secondary = models.CharField(
        max_length=7, default='#3a5454',
        help_text='Muted/secondary text'
    )
    color_text_tertiary = models.CharField(
        max_length=7, default='#6b8a8a',
        help_text='Captions and disabled text'
    )
    
    # Border & Divider Colors
    color_border = models.CharField(
        max_length=7, default='#ccdedd',
        help_text='Input and card borders'
    )
    color_divider = models.CharField(
        max_length=7, default='#dff1f0',
        help_text='Subtle section dividers'
    )
    
    # Status Colors
    color_success = models.CharField(
        max_length=7, default='#28A745',
        help_text='Success messages and indicators'
    )
    color_warning = models.CharField(
        max_length=7, default='#FFC107',
        help_text='Warning messages and indicators'
    )
    color_error = models.CharField(
        max_length=7, default='#DC3545',
        help_text='Error messages and indicators'
    )
    
    # Dark Mode - Background Colors
    dark_bg_primary = models.CharField(
        max_length=7, default='#011a1d',
        help_text='Main page background'
    )
    dark_bg_secondary = models.CharField(
        max_length=7, default='#022a2e',
        help_text='Card and surface background'
    )
    dark_bg_tertiary = models.CharField(
        max_length=7, default='#033b40',
        help_text='Elevated surfaces (modals, dropdowns)'
    )
    
    # Dark Mode - Text Colors
    dark_text_primary = models.CharField(
        max_length=7, default='#ebfdfc',
        help_text='Main body text'
    )
    dark_text_secondary = models.CharField(
        max_length=7, default='#84f5e8',
        help_text='Muted/secondary text'
    )
    dark_text_tertiary = models.CharField(
        max_length=7, default='#5db8ad',
        help_text='Disabled text and placeholders'
    )
    
    # Dark Mode - Border & Divider Colors
    dark_border = models.CharField(
        max_length=7, default='#064e56',
        help_text='Input and card borders'
    )
    dark_divider = models.CharField(
        max_length=7, default='#043f47',
        help_text='Subtle section dividers'
    )

    class Meta:
        verbose_name = 'Site Settings'
        verbose_name_plural = 'Site Settings'

    def __str__(self):
        return 'Site Settings'

    def save(self, *args, **kwargs):
        # Ensure only one instance exists (singleton pattern)
        self.pk = 1
        super().save(*args, **kwargs)
        # Invalidate cache when settings are saved
        cache.delete('site_settings')

    def delete(self, *args, **kwargs):
        # Prevent deletion of the singleton
        pass

    @classmethod
    def get_settings(cls):
        """
        Get the site settings from cache or database.
        Creates default settings if none exist.
        """
        settings = cache.get('site_settings')
        if settings is None:
            settings, created = cls.objects.get_or_create(pk=1)
            cache.set('site_settings', settings, timeout=None)  # Cache indefinitely
        return settings
