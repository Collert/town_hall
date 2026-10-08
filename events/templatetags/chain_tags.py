from django import template
from django.utils import timezone
from django.utils.formats import date_format, time_format

register = template.Library()


@register.filter
def timespan(start, end):
    """"Oct 8, 8:00 a.m.–4:00 p.m.", with both dates when the span crosses midnight."""
    if not start or not end:
        return ''
    start, end = timezone.localtime(start), timezone.localtime(end)
    day = date_format(start, 'M j')
    if start.date() == end.date():
        return f'{day}, {time_format(start)}–{time_format(end)}'
    return f'{day}, {time_format(start)} – {date_format(end, "M j")}, {time_format(end)}'
