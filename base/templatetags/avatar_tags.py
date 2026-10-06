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


@register.filter
def display_name(user):
    """Full name, falling back to the username."""
    return user.get_full_name() or user.username


@register.inclusion_tag('base/partials/avatar.html')
def avatar(user, size='md'):
    """Render a user's avatar image, or colored initials when they have none.

    Sizes: xs, sm, md, lg, xl (see `.avatar-*` in base/styles.css).
    """
    profile = getattr(user, 'profile', None)
    return {
        'name': user.get_full_name() or user.username,
        'image': profile.avatar.url if profile and profile.avatar else None,
        'size': size,
    }
