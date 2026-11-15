"""API keys and heartbeat-tracked shifts for the JSON API (api/views.py, api/shifts.py).

Staff create keys at Console > Organization > API. A key lets another application (for example
a library's admin site, through the `town-hall-connector` Python package) start and end shifts
in permanent roles. Keys are shown once and stored only as SHA-256 hashes.
"""
import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

KEY_PREFIX = 'th_'


def generate_key():
    """Kept for old migrations (api.0003); new keys come from APIKey.create."""
    return secrets.token_hex(16)


def digest(value):
    return hashlib.sha256((value or '').encode()).hexdigest()


class APIKeyQuerySet(models.QuerySet):
    def active(self):
        now = timezone.now()
        return self.filter(revoked_at__isnull=True).filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))


class APIKey(models.Model):
    name = models.CharField(_('name'), max_length=100, help_text=_('Which application uses this key, e.g. "Library admin site".'))
    prefix = models.CharField(max_length=16, editable=False, help_text='The start of the key, shown so staff can tell keys apart')
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    roles = models.ManyToManyField('jobs.Role', blank=True, related_name='api_keys',
                                   help_text='Permanent roles this key may start shifts in. Empty means every permanent role.')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    last_used_at = models.DateTimeField(blank=True, null=True)
    revoked_at = models.DateTimeField(blank=True, null=True)

    objects = APIKeyQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} ({self.prefix}…)'

    @classmethod
    def create(cls, name, created_by=None, roles=(), expires_at=None):
        """Make a key and return (api_key, plaintext). The plaintext can't be recovered later."""
        plaintext = KEY_PREFIX + secrets.token_urlsafe(32)
        key = cls.objects.create(name=name, prefix=plaintext[:len(KEY_PREFIX) + 6], key_hash=digest(plaintext),
                                 created_by=created_by, expires_at=expires_at)
        if roles:
            key.roles.set(roles)
        return key, plaintext

    @classmethod
    def authenticate(cls, plaintext):
        """The active key for this plaintext, or None."""
        if not plaintext:
            return None
        return cls.objects.active().filter(key_hash=digest(plaintext.strip())).first()

    @property
    def is_active(self):
        return not self.revoked_at and not (self.expires_at and self.expires_at <= timezone.now())

    def allows_role(self, role):
        if not role.permanent:
            return False
        return not self.roles.exists() or self.roles.filter(pk=role.pk).exists()

    def revoke(self):
        if not self.revoked_at:
            self.revoked_at = timezone.now()
            self.save(update_fields=['revoked_at'])


class ShiftHeartbeat(models.Model):
    """Marks a shift the API started as alive. If no heartbeat arrives for
    api.shifts.HEARTBEAT_TIMEOUT, the shift ends at `last_seen` (api.shifts.end_stale_shifts)."""
    shift = models.OneToOneField('jobs.Shift', on_delete=models.CASCADE, related_name='heartbeat')
    api_key = models.ForeignKey(APIKey, on_delete=models.SET_NULL, null=True, blank=True, related_name='heartbeats')
    last_seen = models.DateTimeField(default=timezone.now, db_index=True)

    def __str__(self):
        return f'Heartbeat for {self.shift} at {self.last_seen}'
