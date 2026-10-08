from datetime import timedelta

from django.db import models
from django.core.cache import cache
from django.contrib.auth.models import User
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

import secrets
import string

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


class EmailTrigger(models.Model):
    """Whether an app event sends an email, and with which listmonk template.

    The events themselves are defined in base/triggers.py; a missing row means the
    trigger's defaults. Edited in console > Communication > Email.
    """
    key = models.CharField(max_length=40, unique=True)
    enabled = models.BooleanField(default=False)
    auto_send = models.BooleanField(default=False, help_text='Campaign triggers: start the campaign instead of leaving a draft')
    extra_list_ids = models.JSONField(default=list, blank=True, help_text='Campaign triggers: listmonk lists to send to besides "Town Hall users"')

    def __str__(self):
        return self.key


class EmailTemplate(models.Model):
    """The listmonk template an email trigger uses in one language.

    Seeded by Town Hall when listmonk is connected (one per trigger and enabled
    language); staff can point a row at any other listmonk template.
    """
    trigger = models.CharField(max_length=40)
    language = models.CharField(max_length=10)
    template_id = models.PositiveIntegerField()

    class Meta:
        unique_together = ('trigger', 'language')

    def __str__(self):
        return f'{self.trigger} ({self.language}) -> #{self.template_id}'

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
    id_code = models.CharField(max_length=6, unique=True, editable=False, help_text='Unique identifier for the user (e.g., employee ID, volunteer ID)')
    admin_notes = models.TextField(blank=True, default='', help_text='Internal notes visible to staff only')
    language = models.CharField(max_length=10, blank=True, default='', help_text='Language the user last browsed in; emails use it')

    @property
    def level(self):
        """Determine the user's level based on impact points."""
        return Level.objects.filter(min_points__lte=self.impact_points).order_by('min_points').last()

    @property
    def next_level(self):
        return Level.objects.filter(min_points__gt=self.impact_points).order_by('min_points').first()

    @property
    def level_progress(self):
        """Percentage of the way from the current level to the next one."""
        level, next_level = self.level, self.next_level
        if not next_level:
            return 100 if level else 0
        floor = level.min_points if level else 0
        span = next_level.min_points - floor
        return min(100, round((self.impact_points - floor) / span * 100)) if span > 0 else 100

    def hours_contributed(self):
        """Total hours from completed shifts."""
        return round(sum(s.duration() for s in self.user.shifts.filter(end_time__isnull=False)), 1)

    def recalculate_impact_points(self):
        total = self.user.points_entries.aggregate(total=models.Sum('amount'))['total']
        self.impact_points = total or 0
        self.save(update_fields=['impact_points'])

    def __str__(self):
        return f"{self.user.username} Profile"
    
    @staticmethod
    def generate_random_string(length=6):
        # Generates a secure random alphanumeric string
        alphabet = string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(length))
    
    @classmethod
    def unique_id_code(cls):
        """A random 6-digit kiosk code no other profile has."""
        while True:
            code = cls.generate_random_string(length=6)
            if not cls.objects.filter(id_code=code).exists():
                return code

    def reset_id_code(self):
        """Replace the kiosk code, e.g. after someone else saw it. The old one stops working."""
        old = self.id_code
        while self.id_code == old:
            self.id_code = self.unique_id_code()
        self.save(update_fields=['id_code'])

    def save(self, *args, **kwargs):
        # Only generate a code if the profile doesn't have one yet
        if not self.id_code:
            self.id_code = self.unique_id_code()
        super().save(*args, **kwargs)

class VenueFeature(models.Model):
    """Something a venue offers (stage, full kitchen, restrooms...). Seeded with defaults; staff can add more."""
    CATEGORY_CHOICES = [
        ('facilities', _('Facilities')),
        ('accessibility', _('Accessibility')),
        ('equipment', _('Equipment & tech')),
        ('getting_there', _('Getting there')),
    ]
    name = models.CharField(max_length=100)
    icon = models.CharField(max_length=50, default='check_circle', help_text='Google Material Symbols icon name')
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='facilities')
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class Venue(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    photo = models.ImageField(upload_to='venue_photos/', blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    address = models.CharField(max_length=255, blank=True, null=True)
    capacity = models.PositiveIntegerField(blank=True, null=True)
    latitude = models.FloatField(blank=True, null=True)
    longitude = models.FloatField(blank=True, null=True)
    features = models.ManyToManyField(VenueFeature, related_name='venues', blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        from .geo import geocode
        if self.pk:
            old_address = Venue.objects.filter(pk=self.pk).values_list('address', flat=True).first()
            if old_address != self.address:
                self.latitude = self.longitude = None
        if self.address and self.latitude is None and self.longitude is None:
            self.latitude, self.longitude = geocode(self.address) or (None, None)
        super().save(*args, **kwargs)
        # Events held here use the venue's coordinates for distance search.
        self.events.exclude(latitude=self.latitude, longitude=self.longitude).update(
            latitude=self.latitude, longitude=self.longitude)

    @property
    def map_url(self):
        from .geo import map_url
        return map_url(self.latitude, self.longitude, self.address)

    @property
    def map_embed_url(self):
        from .geo import map_embed_url
        return map_embed_url(self.latitude, self.longitude)

    def active_notes(self):
        now = timezone.now()
        return [note for note in self.notes.order_by('-created_at') if note.created_at + note.expires_after > now]

    def weekly_hours(self):
        """[{'day', 'label', 'hours' (OperatingHour or None), 'today'}] for Monday..Sunday."""
        by_day = {h.day_of_week: h for h in self.operating_hours.all()}
        today = timezone.localdate().weekday()
        return [
            {'day': day, 'label': label, 'hours': by_day.get(day), 'today': day == today}
            for day, label in OperatingHour.DAY_CHOICES
        ]

    def is_open_now(self):
        """True/False from today's hours, or None when no hours are set at all."""
        hours = list(self.operating_hours.all())
        if not hours:
            return None
        now = timezone.localtime()
        today = next((h for h in hours if h.day_of_week == now.weekday()), None)
        return bool(today and today.open_time <= now.time() < today.close_time)

    def grouped_features(self):
        """[(category label, [features])] in category order, skipping empty categories."""
        features = list(self.features.all())
        return [
            (label, [f for f in features if f.category == key])
            for key, label in VenueFeature.CATEGORY_CHOICES
            if any(f.category == key for f in features)
        ]

class VenueNote(models.Model):
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name='notes')
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_after = models.DurationField(default=timedelta(days=7))

    def __str__(self):
        return f"Note for {self.venue.name} at {self.created_at}"

    @property
    def expires_at(self):
        return self.created_at + self.expires_after


class OperatingHour(models.Model):
    DAY_CHOICES = [
        (0, _('Monday')), (1, _('Tuesday')), (2, _('Wednesday')), (3, _('Thursday')),
        (4, _('Friday')), (5, _('Saturday')), (6, _('Sunday')),
    ]
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name='operating_hours')
    day_of_week = models.PositiveSmallIntegerField(choices=DAY_CHOICES)
    open_time = models.TimeField()
    close_time = models.TimeField()
    open_to_staff_outside_hours = models.BooleanField(default=False)

    class Meta:
        unique_together = ('venue', 'day_of_week')

    def __str__(self):
        return f"{self.venue.name} - {self.get_day_of_week_display()}: {self.open_time} to {self.close_time}"
class Endorsement(models.Model):
    endorser = models.ForeignKey(User, on_delete=models.CASCADE, related_name='given_endorsements')
    endorsed = models.ForeignKey(User, on_delete=models.CASCADE, related_name='endorsements')
    skills = models.ManyToManyField('education.Skill', related_name='endorsements')
    event = models.ForeignKey('events.Event', on_delete=models.SET_NULL, blank=True, null=True, related_name='endorsements', help_text='Event the endorsement relates to, if any')
    timestamp = models.DateTimeField(auto_now_add=True)
    text = models.TextField(blank=True, default='')

    def __str__(self):
        return f"{self.endorser.username} endorsed {self.endorsed.username}"

    @classmethod
    def give(cls, endorser, endorsed, skills, text='', event=None):
        """One endorsement covering all `skills`. A person endorses each skill once, so the
        skills move out of the endorser's earlier endorsements of the same person."""
        skill_ids = [skill.pk for skill in skills]
        cls.skills.through.objects.filter(
            endorsement__endorser=endorser, endorsement__endorsed=endorsed, skill_id__in=skill_ids).delete()
        cls.objects.filter(endorser=endorser, endorsed=endorsed, skills__isnull=True).delete()
        endorsement = cls.objects.create(endorser=endorser, endorsed=endorsed, text=text, event=event)
        endorsement.skills.set(skill_ids)
        from .points import award_endorsement
        award_endorsement(endorsement)
        return endorsement

class PointsRules(models.Model):
    """Singleton: how impact points are awarded. Tuned in Console > Organization > Points."""
    points_per_hour = models.PositiveIntegerField(default=10, help_text='Base points for every hour worked')
    max_role_weight = models.DecimalField(max_digits=3, decimal_places=1, default=2.0,
                                          help_text='Multiplier for the most demanding roles (by training length); the easiest get 1.0')
    reach_cap_percent = models.PositiveIntegerField(default=50, help_text='Largest bonus for serving many people')
    walk_in_bonus_percent = models.PositiveIntegerField(default=50, help_text='Covering an open slot at the kiosk without signing up')
    last_minute_bonus_percent = models.PositiveIntegerField(default=25, help_text='Signing up for an understaffed slot shortly before it starts')
    last_minute_window_hours = models.PositiveIntegerField(default=24)
    off_hours_bonus_percent = models.PositiveIntegerField(default=25, help_text='Shifts that start early or end late')
    early_start_hour = models.PositiveSmallIntegerField(default=7, help_text='Shifts starting before this hour get the off-hours bonus')
    late_end_hour = models.PositiveSmallIntegerField(default=22, help_text='Shifts ending after this hour get the off-hours bonus')
    training_points_per_minute = models.DecimalField(max_digits=4, decimal_places=1, default=1.0)
    training_points_min = models.PositiveIntegerField(default=5, help_text='Smallest award for finishing a module')
    training_points_cap = models.PositiveIntegerField(default=60, help_text='Largest award for finishing a module')
    endorsement_points = models.PositiveIntegerField(default=5, help_text='Awarded to someone endorsed by a teammate from the same event')
    endorsement_monthly_cap = models.PositiveIntegerField(default=5, help_text='Endorsement awards a person can earn in 30 days')

    class Meta:
        verbose_name_plural = 'Points rules'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete('points_rules')

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def get(cls):
        rules = cache.get('points_rules')
        if rules is None:
            rules, _created = cls.objects.get_or_create(pk=1)
            cache.set('points_rules', rules, timeout=None)
        return rules

    def __str__(self):
        return 'Points rules'


class PointsEntry(models.Model):
    """One line of a volunteer's impact points ledger. Their total is the sum of these, and each
    entry keeps the amount it was given with, so changing the rules never rewrites history."""
    SHIFT, TRAINING, ENDORSEMENT, ADJUSTMENT = 'shift', 'training', 'endorsement', 'adjustment'
    SOURCES = [
        (SHIFT, _('Shift')),
        (TRAINING, _('Training')),
        (ENDORSEMENT, _('Endorsement')),
        (ADJUSTMENT, _('Adjustment')),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='points_entries')
    amount = models.IntegerField()
    source = models.CharField(max_length=20, choices=SOURCES)
    reason = models.CharField(max_length=200, blank=True, default='', help_text='Shown for manual adjustments')
    details = models.JSONField(default=dict, blank=True, help_text='How the amount was worked out')
    shift = models.OneToOneField('jobs.Shift', on_delete=models.CASCADE, null=True, blank=True, related_name='points_entry')
    training_module = models.ForeignKey('education.TrainingModule', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    endorsement = models.ForeignKey(Endorsement, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    event = models.ForeignKey('events.Event', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(default=timezone.now, help_text='When the points were earned')

    class Meta:
        ordering = ['-created_at', '-pk']
        verbose_name_plural = 'Points entries'

    def __str__(self):
        return f"{self.amount:+} {self.source} for {self.user.username}"

    @property
    def label(self):
        from .points import entry_label
        return entry_label(self)

    @property
    def factors(self):
        from .points import entry_factors
        return entry_factors(self)


@receiver(post_save, sender='education.TrainingModuleCompletion')
def award_training_points(sender, instance, created, **kwargs):
    if created:
        from .points import award_training
        award_training(instance)


@receiver(post_save, sender=PointsEntry)
@receiver(post_delete, sender=PointsEntry)
def update_points_total(sender, instance, **kwargs):
    profile = Profile.objects.filter(user_id=instance.user_id).first()
    if profile:
        profile.recalculate_impact_points()


class AdminFeedback(models.Model):
    """Private staff rating of a volunteer. Never shown to the volunteer."""
    volunteer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='admin_feedback')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='authored_admin_feedback')
    rating = models.PositiveSmallIntegerField(choices=[(i, str(i)) for i in range(1, 6)])
    note = models.TextField(blank=True, default='')
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.rating}/5 for {self.volunteer.username}"

@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)


@receiver(post_save, sender=User)
def sync_user_to_listmonk(sender, instance, created, update_fields=None, **kwargs):
    # Logging in only saves last_login; nothing listmonk cares about changed.
    if update_fields and set(update_fields) <= {'last_login', 'password'}:
        return
    from . import listmonk
    listmonk.queue_sync(instance)


class BellTowerLink(models.Model):
    """The Bell Tower account a Town Hall user acts as (base/belltower.py), matched by email
    and created there when missing. Scoped to a server, so reconnecting Town Hall to
    another Bell Tower links everyone afresh."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='belltower_links')
    belltower_url = models.URLField()
    username = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'belltower_url'], name='unique_belltower_link')]

    def __str__(self):
        return f'{self.user} -> {self.username} @ {self.belltower_url}'


@receiver(post_save, sender=User)
def link_new_user_to_belltower(sender, instance, created, **kwargs):
    if created:
        from . import belltower
        belltower.queue_link(instance)


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
    # Plain text so site-relative links like /en/opportunities/ are allowed.
    button_1_url = models.CharField(max_length=200, default='#')
    button_2_text = models.CharField(max_length=20, default='Get Involved')
    button_2_url = models.CharField(max_length=200, default='#')

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

    logo = models.ImageField(upload_to='branding/', blank=True, null=True, help_text='Organization logo (PNG or SVG)')
    contact_email = models.EmailField(blank=True, default='', help_text='Public contact address')
    terms_of_service = models.TextField(blank=True, default='', help_text='Terms of service shown to volunteers (Markdown)')

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

    # Backend integrations (console > Organization > Backend). Empty means "use the
    # matching environment variable, if any".
    libretranslate_url = models.URLField(blank=True, default='', help_text='Your LibreTranslate server, e.g. http://translate.internal:5000')
    libretranslate_api_key = models.CharField(max_length=200, blank=True, default='')
    google_translate_api_key = models.CharField(max_length=200, blank=True, default='', help_text='Google Cloud Translation API key')
    deepl_api_key = models.CharField(max_length=200, blank=True, default='', help_text='DeepL API key (free-tier keys end in ":fx")')
    mymemory_email = models.EmailField(blank=True, default='', help_text='Raises the free MyMemory limit to about 50,000 characters a day')
    # Email goes through listmonk (base/listmonk.py). Templates and lists are managed there;
    # these IDs point at them. With no listmonk URL, emails are printed to the server console.
    listmonk_url = models.URLField(blank=True, default='', help_text='Your listmonk server, e.g. https://lists.example.org')
    listmonk_api_user = models.CharField(max_length=200, blank=True, default='', help_text='API user created in listmonk (Admin > Users)')
    listmonk_api_token = models.CharField(max_length=200, blank=True, default='')
    listmonk_list_id = models.PositiveIntegerField(null=True, blank=True, help_text='The "Town Hall users" list everyone is synced into')
    # Branding copied into listmonk: the logo as uploaded to listmonk's media library, and
    # the brand values last written into the seeded templates (see base/email_templates.py).
    listmonk_logo_url = models.URLField(max_length=500, blank=True, default='')
    listmonk_logo_source = models.CharField(max_length=255, blank=True, default='', help_text='Logo file the listmonk copy was made from')
    listmonk_brand = models.JSONField(default=dict, blank=True)
    default_from_email = models.CharField(max_length=200, blank=True, default='', help_text='Sender, e.g. "Town Hall <hello@example.org>". Blank uses listmonk\'s default.')
    # Bell Tower: shared task lists (base/belltower.py). Set by the connect flow in
    # Organization > Backend, never typed in: the API key is issued by Bell Tower.
    belltower_url = models.URLField(blank=True, default='', help_text='Bell Tower server, e.g. https://tasks.example.org')
    belltower_api_key = models.CharField(max_length=200, blank=True, default='')
    belltower_username = models.CharField(max_length=150, blank=True, default='', help_text='Bell Tower account Town Hall acts as')
    belltower_endpoints = models.JSONField(default=dict, blank=True, help_text='From /.well-known/belltower')
    belltower_connected_at = models.DateTimeField(null=True, blank=True)

    kiosk_idle_timeout_seconds = models.PositiveSmallIntegerField(
        default=30,
        help_text='Idle time in seconds before kiosk sessions are automatically logged out'
    )

    # Console > Organization > Region. Applied at startup (town_hall/site_config.py); empty uses the settings file.
    languages = models.CharField(max_length=100, blank=True, default='', help_text='Comma-separated language codes that are switched on')
    default_language = models.CharField(max_length=10, blank=True, default='')
    time_zone = models.CharField(max_length=64, blank=True, default='')

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

    def language_codes(self):
        return [code for code in self.languages.split(',') if code]

    def backend_value(self, field, env_var):
        """A backend setting saved in the console, falling back to the environment variable."""
        import os
        return getattr(self, field) or os.environ.get(env_var, '')

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
