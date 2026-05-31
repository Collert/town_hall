import markdown
from django import template
from django.template.defaultfilters import stringfilter
from django.utils.safestring import mark_safe

register = template.Library()

@register.filter
@stringfilter
def render_markdown(value):
    # mark_safe ensures the HTML isn't escaped by Django
    return mark_safe(markdown.markdown(value, extensions=['fenced_code', 'codehilite']))