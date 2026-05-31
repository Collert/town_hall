import hashlib

from django import template

register = template.Library()


@register.filter
def initials(name):
    """Return up to 2 initials from a name string."""
    parts = str(name).strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    elif len(parts) == 1 and parts[0]:
        return parts[0][0].upper()
    return '?'


@register.filter
def avatar_color(name):
    """Return a deterministic HSL background color derived from the name."""
    digest = int(hashlib.sha256(str(name).encode('utf-8')).hexdigest(), 16)
    hue = digest % 360
    return f'hsl({hue}, 50%, 42%)'
