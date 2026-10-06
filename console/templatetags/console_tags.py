import re

from django import template

from education.models import UserCertification

register = template.Library()


@register.simple_tag
def pending_verification_count():
    """Certifications with uploaded documents still awaiting review."""
    return UserCertification.objects.filter(
        verified=False, rejected=False, files__isnull=False,
    ).distinct().count()


@register.filter
def percent_of(part, whole):
    """Integer percentage of part/whole, capped at 100."""
    try:
        whole = float(whole)
        return min(100, round(float(part) / whole * 100)) if whole else 0
    except (TypeError, ValueError):
        return 0


@register.filter
def strip_lang(label):
    """'Title [en]' -> 'Title' (modeltranslation appends the language code)."""
    return re.sub(r'\s*\[[\w-]+\]$', '', str(label))


@register.filter
def base_field(name):
    """'content_es' -> 'content' (strip the modeltranslation language suffix)."""
    return re.sub(r'_[a-z]{2}$', '', str(name))


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def hours(value):
    """Format a float hour count: 3.0 -> 3, 2.25 -> 2.3."""
    try:
        value = round(float(value), 1)
    except (TypeError, ValueError):
        return value
    return int(value) if value == int(value) else value
