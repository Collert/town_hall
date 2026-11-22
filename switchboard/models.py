"""Connections to Switchboard, the organization's workflow builder (see switchboard/__init__.py
for the map of this app and Switchboard's docs/PROTOCOL.md for the contract).

A Switchboard connects itself: staff approve it on the consent page, it trades the one-time
code for an API key and a webhook secret, and from then on Town Hall posts its events to the
connection's webhook and runs the actions Switchboard asks for.
"""
import hashlib
import secrets
from urllib.parse import urlsplit

from django.conf import settings
from django.db import models
from django.utils import timezone

KEY_PREFIX = 'sbk_'


def digest(value):
    return hashlib.sha256((value or '').encode()).hexdigest()


class SwitchboardConnectionQuerySet(models.QuerySet):
    def active(self):
        """Connections that still receive events (a broken one stays listed but gets nothing)."""
        return self.filter(broken_at__isnull=True)


class SwitchboardConnection(models.Model):
    """One Switchboard this Town Hall talks to. The API key is kept only as a SHA-256 hash;
    the webhook secret signs every delivery, so it is stored as is, like the other service
    credentials on SiteSettings."""
    name = models.CharField(max_length=200, blank=True, default='', help_text="The Switchboard's organization name")
    webhook_url = models.URLField(max_length=500)
    base_url = models.CharField(max_length=300, blank=True, default='',
                                help_text='This Town Hall as the Switchboard reached it, e.g. https://volunteers.example.org')
    key_prefix = models.CharField(max_length=16, editable=False)
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    webhook_secret = models.CharField(max_length=100, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True, help_text='Last API call from Switchboard')
    last_delivery_at = models.DateTimeField(null=True, blank=True, help_text='Last event that reached Switchboard')
    failures = models.PositiveIntegerField(default=0, help_text='Delivery attempts that failed since the last success')
    last_error = models.CharField(max_length=300, blank=True, default='')
    broken_at = models.DateTimeField(null=True, blank=True, help_text='Switchboard answered 410: it no longer knows this connection')

    objects = SwitchboardConnectionQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name or self.host} ({self.key_prefix}…)'

    @classmethod
    def create(cls, webhook_url, name='', base_url='', created_by=None):
        """Make a connection and return (connection, api_key). The key can't be recovered later."""
        api_key = KEY_PREFIX + secrets.token_urlsafe(32)
        connection = cls.objects.create(
            name=name[:200], webhook_url=webhook_url, base_url=base_url[:300], created_by=created_by,
            key_prefix=api_key[:len(KEY_PREFIX) + 6], key_hash=digest(api_key),
            webhook_secret=secrets.token_urlsafe(32),
        )
        return connection, api_key

    @classmethod
    def authenticate(cls, api_key):
        """The connection for this key, or None."""
        if not api_key:
            return None
        return cls.objects.filter(key_hash=digest(api_key.strip())).first()

    @property
    def host(self):
        return urlsplit(self.webhook_url).netloc

    @property
    def state(self):
        """'broken' (Switchboard forgot us), 'failing' (recent deliveries didn't arrive) or 'ok'."""
        if self.broken_at:
            return 'broken'
        return 'failing' if self.failures else 'ok'


class AuthorizeCode(models.Model):
    """The one-time code a staff member's approval hands to Switchboard (10 minutes, single use)."""
    code_hash = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    name = models.CharField(max_length=200, blank=True, default='')
    callback = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'Code for {self.name or self.callback} by {self.user}'


class IdempotencyRecord(models.Model):
    """The answer given to an action call, replayed when the same Idempotency-Key comes again
    so a retried workflow step never does its work twice. Pruned after two weeks."""
    connection = models.ForeignKey(SwitchboardConnection, on_delete=models.CASCADE, related_name='idempotency_records')
    key = models.CharField(max_length=200)
    action = models.CharField(max_length=100)
    status = models.PositiveSmallIntegerField()
    response = models.JSONField()
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['connection', 'key'], name='unique_switchboard_idempotency_key')]

    def __str__(self):
        return f'{self.action} {self.key} -> {self.status}'


class Delivery(models.Model):
    """One event on its way to one connection: the delivery log staff see, and the retry queue.
    `body` is the exact JSON that is signed and sent, kept until the delivery ends either way."""
    PENDING, DELIVERED, FAILED = 'pending', 'delivered', 'failed'
    STATUSES = [(PENDING, 'Pending'), (DELIVERED, 'Delivered'), (FAILED, 'Failed')]

    connection = models.ForeignKey(SwitchboardConnection, on_delete=models.CASCADE, related_name='deliveries')
    event_id = models.CharField(max_length=40, db_index=True)
    event_type = models.CharField(max_length=100)
    summary = models.CharField(max_length=200, blank=True, default='')
    body = models.TextField(blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    error = models.CharField(max_length=300, blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        verbose_name_plural = 'Deliveries'

    def __str__(self):
        return f'{self.event_type} {self.event_id} -> {self.connection_id} ({self.status})'
