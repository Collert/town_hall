"""OAuth records for the MCP connector (mcp_server/oauth.py).

AI apps such as Claude register themselves as clients, a staff member approves the
connection while signed in, and the app gets an access token that acts as that person.
Codes and tokens are stored only as SHA-256 hashes.
"""
import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


def new_secret(prefix):
    return f'{prefix}_{secrets.token_urlsafe(32)}'


def digest(value):
    return hashlib.sha256((value or '').encode()).hexdigest()


class OAuthClient(models.Model):
    """An AI app that registered itself (RFC 7591 dynamic client registration)."""
    client_id = models.CharField(max_length=64, unique=True)
    secret_hash = models.CharField(max_length=64, blank=True, default='', help_text='Empty for public clients')
    name = models.CharField(max_length=200, blank=True, default='')
    redirect_uris = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name or self.client_id


class AuthorizationCode(models.Model):
    """One-time code handed to the client after approval; traded for tokens with PKCE."""
    code_hash = models.CharField(max_length=64, unique=True)
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE, related_name='codes')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='mcp_codes')
    redirect_uri = models.URLField(max_length=500)
    code_challenge = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)


class AccessToken(models.Model):
    """A connection: an access token (short-lived) and the refresh token that renews it.
    Refreshing replaces the row's tokens, so one row is one connected app session."""
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE, related_name='tokens')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='mcp_tokens')
    access_hash = models.CharField(max_length=64, unique=True)
    refresh_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    refresh_expires_at = models.DateTimeField()
    revoked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.client} for {self.user}'

    @property
    def active(self):
        return not self.revoked and self.refresh_expires_at > timezone.now()
