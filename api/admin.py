from django.contrib import admin

from .models import APIKey, ShiftHeartbeat


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    """Read-mostly: keys are created and revoked at Console > Organization > API."""
    list_display = ['name', 'prefix', 'created_by', 'created_at', 'last_used_at', 'expires_at', 'revoked_at']
    readonly_fields = ['prefix', 'key_hash', 'created_by', 'created_at', 'last_used_at']

    def has_add_permission(self, request):
        return False


@admin.register(ShiftHeartbeat)
class ShiftHeartbeatAdmin(admin.ModelAdmin):
    list_display = ['shift', 'api_key', 'last_seen']
