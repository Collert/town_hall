from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from modeltranslation.admin import TranslationAdmin, TabbedTranslationAdmin
from .models import HeroSection, Level, Notification, SiteSettings, Profile

admin.site.register(Notification)
admin.site.register(Profile)
admin.site.register(Level)

class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'profile'

class UserAdmin(BaseUserAdmin):
    inlines = (ProfileInline,)

    def get_inline_instances(self, request, obj=None):
        if not obj:
            return list()
        return super(UserAdmin, self).get_inline_instances(request, obj)

# Re-register UserAdmin
admin.site.unregister(User)
admin.site.register(User, UserAdmin)

@admin.register(HeroSection)
class HeroSectionAdmin(TabbedTranslationAdmin):
    """Admin with tabbed translation fields for HeroSection."""
    list_display = ('title', 'subtitle', 'button_1_text', 'button_2_text')

    def has_add_permission(self, request):
        # Only allow one instance (singleton)
        return not HeroSection.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Prevent deletion
        return False

@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """Admin interface for site settings with grouped color fields."""
    
    fieldsets = (
        ('General', {
            'fields': ('company_name',),
        }),
        ('Primary Colors', {
            'fields': ('color_primary', 'color_primary_contrast', 'color_primary_dark_offset', 'color_primary_light_offset'),
        }),
        ('Accent Colors', {
            'fields': ('color_accent', 'color_accent_contrast', 'color_accent_dark_offset', 'color_accent_light_offset'),
        }),
        ('Background Colors', {
            'fields': ('color_bg_primary', 'color_bg_secondary', 'color_bg_tertiary'),
        }),
        ('Text Colors', {
            'fields': ('color_text_primary', 'color_text_secondary', 'color_text_tertiary'),
        }),
        ('Border & Divider Colors', {
            'fields': ('color_border', 'color_divider'),
        }),
        ('Status Colors', {
            'fields': ('color_success', 'color_warning', 'color_error'),
        }),
        ('Dark Mode - Backgrounds', {
            'fields': ('dark_bg_primary', 'dark_bg_secondary', 'dark_bg_tertiary'),
            'classes': ('collapse',),
        }),
        ('Dark Mode - Text', {
            'fields': ('dark_text_primary', 'dark_text_secondary', 'dark_text_tertiary'),
            'classes': ('collapse',),
        }),
        ('Dark Mode - Borders', {
            'fields': ('dark_border', 'dark_divider'),
            'classes': ('collapse',),
        }),
    )

    def has_add_permission(self, request):
        # Only allow one instance (singleton)
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Prevent deletion
        return False
