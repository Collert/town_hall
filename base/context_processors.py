from .models import SiteSettings


def site_settings(request):
    """
    Context processor that adds site settings (including theme colors)
    to all template contexts. Uses caching for performance.
    """
    settings = SiteSettings.get_settings()
    
    return {
        'site_settings': settings,
        'company_name': settings.company_name,
        'theme_colors': {
            # Primary Colors
            'primary': settings.color_primary,
            'primary_contrast': settings.color_primary_contrast,
            'primary_dark_offset': settings.color_primary_dark_offset,
            'primary_light_offset': settings.color_primary_light_offset,
            # Accent Colors
            'accent': settings.color_accent,
            'accent_contrast': settings.color_accent_contrast,
            'accent_dark_offset': settings.color_accent_dark_offset,
            'accent_light_offset': settings.color_accent_light_offset,
            # Background Colors
            'bg_primary': settings.color_bg_primary,
            'bg_secondary': settings.color_bg_secondary,
            'bg_tertiary': settings.color_bg_tertiary,
            # Text Colors
            'text_primary': settings.color_text_primary,
            'text_secondary': settings.color_text_secondary,
            'text_tertiary': settings.color_text_tertiary,
            # Border & Divider Colors
            'border': settings.color_border,
            'divider': settings.color_divider,
            # Status Colors
            'success': settings.color_success,
            'warning': settings.color_warning,
            'error': settings.color_error,
            # Dark Mode - Background Colors
            'dark_bg_primary': settings.dark_bg_primary,
            'dark_bg_secondary': settings.dark_bg_secondary,
            'dark_bg_tertiary': settings.dark_bg_tertiary,
            # Dark Mode - Text Colors
            'dark_text_primary': settings.dark_text_primary,
            'dark_text_secondary': settings.dark_text_secondary,
            'dark_text_tertiary': settings.dark_text_tertiary,
            # Dark Mode - Border & Divider Colors
            'dark_border': settings.dark_border,
            'dark_divider': settings.dark_divider,
        }
    }


def user_notifications(request):
    """
    Context processor that adds unread notifications and their count
    to all template contexts for authenticated users.
    """
    if request.user.is_authenticated:
        unread_notifications = request.user.notifications.filter(read=False).order_by('-timestamp')
        return {
            'unread_notifications': unread_notifications,
            'unread_notifications_count': unread_notifications.count(),
        }
    return {}

