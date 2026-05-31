from django import forms
from .models import SiteSettings


class ColorInput(forms.TextInput):
    """Custom widget for color input fields."""
    input_type = 'color'


class SiteSettingsForm(forms.ModelForm):
    """Form for editing site theme colors."""
    
    class Meta:
        model = SiteSettings
        exclude = []  # Include all fields
        widgets = {
            # Primary Colors
            'color_primary': ColorInput(),
            'color_primary_contrast': ColorInput(),
            'color_primary_dark': ColorInput(),
            'color_primary_light': ColorInput(),
            # Accent Colors
            'color_accent': ColorInput(),
            'color_accent_contrast': ColorInput(),
            'color_accent_dark': ColorInput(),
            'color_accent_light': ColorInput(),
            # Background Colors
            'color_bg_primary': ColorInput(),
            'color_bg_secondary': ColorInput(),
            'color_bg_tertiary': ColorInput(),
            # Text Colors
            'color_text_primary': ColorInput(),
            'color_text_secondary': ColorInput(),
            'color_text_tertiary': ColorInput(),
            # Border & Divider Colors
            'color_border': ColorInput(),
            'color_divider': ColorInput(),
            # Status Colors
            'color_success': ColorInput(),
            'color_warning': ColorInput(),
            'color_error': ColorInput(),
            # Dark Mode - Background Colors
            'dark_bg_primary': ColorInput(),
            'dark_bg_secondary': ColorInput(),
            'dark_bg_tertiary': ColorInput(),
            # Dark Mode - Text Colors
            'dark_text_primary': ColorInput(),
            'dark_text_secondary': ColorInput(),
            'dark_text_tertiary': ColorInput(),
            # Dark Mode - Border & Divider Colors
            'dark_border': ColorInput(),
            'dark_divider': ColorInput(),
        }
