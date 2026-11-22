from django.contrib import admin

from .models import Delivery, SwitchboardConnection


@admin.register(SwitchboardConnection)
class SwitchboardConnectionAdmin(admin.ModelAdmin):
    """Read-mostly: connections are made from Switchboard and removed at Console > Organization > Backend."""
    list_display = ['name', 'host', 'key_prefix', 'created_by', 'created_at', 'last_delivery_at', 'failures', 'broken_at']
    exclude = ['webhook_secret']
    readonly_fields = ['key_prefix', 'key_hash', 'created_by', 'created_at', 'last_used_at', 'last_delivery_at']

    def has_add_permission(self, request):
        return False


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ['event_type', 'connection', 'status', 'attempts', 'response_status', 'created_at']
    list_filter = ['status', 'event_type']
    exclude = ['body']

    def has_add_permission(self, request):
        return False
