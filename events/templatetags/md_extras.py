import re

import markdown
from django import template
from django.template.defaultfilters import stringfilter
from django.utils.safestring import mark_safe

register = template.Library()

TABLE_DIVIDER = re.compile(r'^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$')


def _space_tables(text):
    """Python-Markdown only sees a table after a blank line; GitHub doesn't need one, so add it."""
    lines = text.replace('\r\n', '\n').split('\n')
    out, fenced = [], False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
        starts_table = (not fenced and '|' in line and i + 1 < len(lines) and '|' in lines[i + 1]
                        and TABLE_DIVIDER.match(lines[i + 1]))
        if starts_table and out and out[-1].strip():
            out.append('')
        out.append(line)
    return '\n'.join(out)


@register.filter
@stringfilter
def render_markdown(value):
    html = markdown.markdown(_space_tables(value), extensions=['fenced_code', 'codehilite', 'tables'])
    # Wide tables scroll sideways on phones instead of stretching the page.
    html = html.replace('<table>', '<div class="md-table"><table>').replace('</table>', '</table></div>')
    # mark_safe ensures the HTML isn't escaped by Django
    return mark_safe(html)
