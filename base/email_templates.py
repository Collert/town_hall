"""Email templates Town Hall seeds into listmonk.

One template per email trigger (base/triggers.py) and enabled language. The seeds
are Django templates in ``base/templates/base/listmonk/`` rendered under each
language, so every string is translatable; they output listmonk (Go) templates.
In a seed, ``tx.x`` prints {{ .Tx.Data.x }}, ``tx_if.x`` prints {{ if .Tx.Data.x }},
``brand.x`` prints {{ $x }} and ``go.end`` / ``go.else`` close Go blocks.

Branding: each transactional seed starts with Go variable definitions ($org_name,
$color_primary, ...) under ``BRAND_MARKER``. ``apply_brand`` rewrites only those
values, so staff edits anywhere else in the template survive identity changes.

"New event published" is a visual campaign template (listmonk's block editor). Its
blocks can't hold Go variables, so brand values are swapped old -> new instead
(``swap_brand``), and the event's details fill ``EVENT_TOKENS`` when a campaign is made.
"""
import html
import json
import re

import markdown
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import translation
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _

from . import triggers

BRAND_MARKER = 'Town Hall brand settings'
NAME_PREFIX = 'Town Hall'

# Placeholders in the visual "New event published" template, filled per event.
EVENT_TOKENS = ('[event_title]', '[event_date]', '[event_location]', '[event_description]')
EVENT_LINK = 'https://town-hall.invalid/event-link'
EVENT_IMAGE = 'https://town-hall.invalid/event-image.png'


def languages():
    """Enabled site languages, default first."""
    codes = [code for code, _name in settings.LANGUAGES]
    return sorted(codes, key=lambda code: code != settings.LANGUAGE_CODE)


def language_name(code):
    return translation.get_language_info(code)['name_local'].capitalize()


def brand_values(site):
    """Brand values copied into listmonk. Keys are the template variable names."""
    return {
        'org_name': site.company_name,
        'logo_url': site.listmonk_logo_url,
        'color_primary': site.color_primary,
        'color_on_primary': site.color_primary_contrast,
        'color_accent': site.color_accent,
        'color_on_accent': site.color_accent_contrast,
        'color_background': site.color_bg_secondary,
        'color_card': site.color_bg_primary,
        'color_text': site.color_text_primary,
        'color_muted': site.color_text_secondary,
        'color_divider': site.color_divider,
    }


def template_name(trigger, lang):
    with translation.override(settings.LANGUAGE_CODE):
        return f'{NAME_PREFIX} · {trigger.label} · {language_name(lang)}'


# ---------------------------------------------------------------------------
# Transactional templates
# ---------------------------------------------------------------------------

class _GoExpressions(dict):
    """Template context mapping where any key renders as a Go template expression."""

    def __init__(self, render):
        super().__init__()
        self.render = render

    def __missing__(self, key):
        return self.render(key)


def _tx_value(key):
    # Safe only takes a string, and a field is nil in listmonk's preview (no data), so HTML
    # fields print only when set.
    return '{{ with .Tx.Data.%s }}{{ . | Safe }}{{ end }}' % key if key.endswith('html') else '{{ .Tx.Data.%s }}' % key


_UNGUARDED_SAFE = re.compile(r'\{\{-?\s*\.Tx\.Data\.(\w+)\s*\|\s*Safe\s*-?\}\}')


def upgrade_body(body):
    """Fix template code seeded by earlier Town Hall versions, leaving everything else as is.

    Unguarded ``{{ .Tx.Data.x | Safe }}`` broke listmonk's preview, where x is nil.
    """
    return _UNGUARDED_SAFE.sub(lambda m: '{{ with .Tx.Data.%s }}{{ . | Safe }}{{ end }}' % m[1], body)


def _go_string(value):
    return json.dumps(str(value), ensure_ascii=False)


def _go_comment(title, rows):
    """A Go template comment (stripped from the sent email) listing name: definition rows."""
    width = max(len(name) for name, _help in rows)
    body = '\n'.join(f'    {name.ljust(width)}  {help_text}' for name, help_text in rows)
    return '{{/*\n  %s\n\n%s\n*/}}' % (title.replace('*/', '* /'), body.replace('*/', '* /'))


def brand_header(brand, trigger=None):
    """Brand variable definitions, plus comments documenting them and the template's data."""
    with translation.override(settings.LANGUAGE_CODE):  # notes for admins, in the default language
        brand_doc = _go_comment(
            f"{BRAND_MARKER}: Town Hall rewrites the values below whenever the organization's identity "
            'changes (Organization > Identity). Keep the variable names; use them anywhere, e.g. $color_primary.',
            [(f'${name}', str(triggers.BRAND_HELP[name])) for name in brand])
        data_doc = ''
        if trigger is not None:
            fields = list(dict.fromkeys(triggers.COMMON_FIELDS + tuple(trigger.fields)))
            data_doc = '\n' + _go_comment(
                f'Data Town Hall sends with "{trigger.label}" emails. Print a field with {{{{ .Tx.Data.<field> }}}}.',
                [(f'.Tx.Data.{name}', str(triggers.FIELD_HELP[name])) for name in fields])
    definitions = '\n'.join('{{ $%s := %s }}' % (name, _go_string(value)) for name, value in brand.items())
    return f'{brand_doc}\n{definitions}{data_doc}'


def apply_brand(body, brand):
    """Rewrite the brand variable values in a seeded transactional template."""
    for name, value in brand.items():
        pattern = r'(\{\{-?\s*\$%s\s*:=\s*)"(?:[^"\\]|\\.)*"' % re.escape(name)
        body = re.sub(pattern, lambda m: m.group(1) + _go_string(value), body)
    return body


def render_tx(trigger, lang, brand):
    """{'name', 'type', 'subject', 'body'} for a transactional template, ready for the API."""
    context = {
        'lang': lang,
        'trigger': trigger,
        'brand_header': mark_safe(brand_header(brand, trigger)),
        'tx': _GoExpressions(_tx_value),
        'tx_if': _GoExpressions(lambda key: '{{ if .Tx.Data.%s }}' % key),
        'brand': _GoExpressions(lambda key: '{{ $%s }}' % key),
        'go': {'else': '{{ else }}', 'end': '{{ end }}', 'if_logo': '{{ if $logo_url }}'},
    }
    with translation.override(lang):
        body = render_to_string(f'base/listmonk/{trigger.key}.html', context)
        subject = str(trigger.subject) % _GoExpressions(lambda key: '{{ .Tx.Data.%s }}' % key)
    return {'name': template_name(trigger, lang), 'type': 'tx', 'subject': subject, 'body': body}


# ---------------------------------------------------------------------------
# Visual campaign template ("New event published")
# ---------------------------------------------------------------------------

def _padding(top, side):
    return {'top': top, 'bottom': top, 'left': side, 'right': side}


def brand_mark_html(brand):
    """The rounded logo tile and organization name, like the admin portal's brand mark.

    Inline styles: this sits in an HTML block in the email body, where a <style> tag isn't
    reliable (Gmail only reads styles in <head>). The transactional seeds use _brand.html.
    """
    name = html.escape(brand['org_name'])
    logo = ''
    if brand['logo_url']:
        logo = (
            f'<td width="48" height="48" align="center" valign="middle" style="width:48px;height:48px;'
            f'border-radius:16px;background-color:{brand["color_background"]};">'
            f'<img src="{html.escape(brand["logo_url"])}" alt="" width="40" style="display:block;margin:0 auto;'
            'width:auto;height:auto;max-width:40px;max-height:40px;border:0;border-radius:10px;"></td>'
            '<td width="12" style="width:12px;font-size:0;line-height:0;">&nbsp;</td>'
        )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'{logo}<td valign="middle" style="font-family:Manrope,Inter,Arial,sans-serif;font-size:18px;'
        f'line-height:1.2;font-weight:800;color:{brand["color_primary"]};">{name}</td></tr></table>'
    )


def visual_blocks(brand):
    """Block list for listmonk's visual editor (email-builder JSON), in the active language."""
    blocks = [('Html', {'style': {'padding': _padding(24, 24)}, 'props': {'contents': brand_mark_html(brand)}})]
    blocks += [
        ('Image', {'style': {'padding': _padding(0, 0)},
                   'props': {'url': EVENT_IMAGE, 'alt': '[event_title]', 'contentAlignment': 'middle'}}),
        ('Text', {'style': {'padding': {'top': 24, 'bottom': 0, 'left': 24, 'right': 24},
                            'color': brand['color_accent'], 'fontSize': 13, 'fontWeight': 'bold'},
                  'props': {'text': _('New opportunity').upper()}}),
        ('Heading', {'style': {'padding': {'top': 4, 'bottom': 8, 'left': 24, 'right': 24}, 'color': brand['color_primary']},
                     'props': {'text': '[event_title]', 'level': 'h1'}}),
        ('Text', {'style': {'padding': _padding(8, 24), 'color': brand['color_muted']},
                  'props': {'markdown': True, 'text': '**[event_date]**  \n[event_location]'}}),
        ('Text', {'style': {'padding': _padding(8, 24)},
                  'props': {'markdown': True, 'text': '[event_description]'}}),
        ('Button', {'style': {'padding': _padding(24, 24), 'textAlign': 'left'},
                    'props': {'text': _('See roles and sign up'), 'url': EVENT_LINK, 'buttonStyle': 'pill',
                              'buttonBackgroundColor': brand['color_accent'], 'buttonTextColor': brand['color_on_accent'],
                              'size': 'large'}}),
        ('Divider', {'style': {'padding': _padding(8, 24)}, 'props': {'lineColor': brand['color_divider']}}),
        ('Text', {'style': {'padding': _padding(16, 24), 'color': brand['color_muted'], 'fontSize': 13},
                  'props': {'markdown': True, 'text': _(
                      "You're receiving this because you have an account with %(org)s. [Unsubscribe]({{UnsubscribeURL}})"
                  ) % {'org': brand['org_name']}}}),
    ]
    return blocks


def _style(style):
    parts = []
    pad = style.get('padding')
    if pad:
        parts.append(f"padding:{pad['top']}px {pad['right']}px {pad['bottom']}px {pad['left']}px")
    for key, css in (('color', 'color'), ('textAlign', 'text-align'), ('fontWeight', 'font-weight')):
        if style.get(key):
            parts.append(f'{css}:{style[key]}')
    if style.get('fontSize'):
        parts.append(f"font-size:{style['fontSize']}px")
    return ';'.join(parts)


def _render_block(kind, data):
    """HTML for one block, close to what listmonk's editor produces (it re-renders on save)."""
    style, props = _style(data.get('style', {})), data.get('props', {})
    if kind == 'Heading':
        size = {'h1': 32, 'h2': 24, 'h3': 20}[props.get('level', 'h2')]
        return f'<{props["level"]} style="font-weight:bold;margin:0;font-size:{size}px;{style}">{html.escape(props["text"])}</{props["level"]}>'
    if kind == 'Text':
        text = markdown.markdown(props['text']) if props.get('markdown') else f'<p>{html.escape(props["text"])}</p>'
        return f'<div style="font-weight:normal;{style}">{text}</div>'
    if kind == 'Image':
        height = f'height:{props["height"]}px;width:auto;' if props.get('height') else 'width:100%;'
        return (f'<div style="{style}"><img alt="{html.escape(props.get("alt", ""))}" src="{props["url"]}" '
                f'style="{height}outline:none;border:none;text-decoration:none;vertical-align:middle;display:inline-block;max-width:100%"/></div>')
    if kind == 'Button':
        return (f'<div style="{style}"><a href="{props["url"]}" target="_blank" style="color:{props["buttonTextColor"]};'
                f'font-size:16px;font-weight:bold;background-color:{props["buttonBackgroundColor"]};border-radius:64px;'
                f'display:inline-block;padding:16px 32px;text-decoration:none">{html.escape(props["text"])}</a></div>')
    if kind == 'Html':
        return f'<div style="{style}">{props["contents"]}</div>'
    if kind == 'Divider':
        return f'<div style="{style}"><hr style="width:100%;border:none;border-top:1px solid {props["lineColor"]};margin:0"/></div>'
    raise ValueError(kind)


def render_visual(trigger, lang, brand):
    """{'name', 'type', 'subject', 'body', 'body_source'} for the visual campaign template."""
    with translation.override(lang):
        blocks = visual_blocks(brand)
    source = {'root': {'type': 'EmailLayout', 'data': {
        'backdropColor': brand['color_background'], 'canvasColor': brand['color_card'],
        'textColor': brand['color_text'], 'fontFamily': 'MODERN_SANS', 'borderRadius': 16,
        'childrenIds': [f'block-{i}' for i in range(len(blocks))],
    }}}
    for i, (kind, data) in enumerate(blocks):
        source[f'block-{i}'] = {'type': kind, 'data': data}
    inner = '\n'.join(_render_block(kind, data) for kind, data in blocks)
    body = (
        f'<!DOCTYPE html>\n<html><body><div style="background-color:{brand["color_background"]};color:{brand["color_text"]};'
        'font-family:&quot;Helvetica Neue&quot;, Arial, sans-serif;font-size:16px;line-height:1.5;margin:0;'
        'padding:32px 0;width:100%">\n<table align="center" width="100%" role="presentation" cellspacing="0" '
        f'cellpadding="0" border="0" style="margin:0 auto;max-width:600px;background-color:{brand["color_card"]};'
        f'border-radius:16px;overflow:hidden"><tbody><tr><td>\n{inner}\n</td></tr></tbody></table></div></body></html>'
    )
    return {'name': template_name(trigger, lang), 'type': 'campaign_visual', 'subject': '',
            'body': body, 'body_source': json.dumps(source, ensure_ascii=False)}


def swap_brand(text, old, new, as_json=False):
    """Replace old brand values with new ones in a visual template's HTML or JSON source."""
    for key, new_value in new.items():
        old_value = old.get(key)
        if not old_value or old_value == new_value:
            continue
        if key.startswith('color_'):
            text = re.sub(re.escape(old_value), new_value, text, flags=re.IGNORECASE)
            continue
        if as_json:  # plain values, and HTML-escaped ones inside the header's HTML block
            for encode in (lambda v: v, html.escape):
                text = text.replace(json.dumps(encode(old_value), ensure_ascii=False)[1:-1],
                                    json.dumps(encode(new_value), ensure_ascii=False)[1:-1])
        else:
            for encode in (lambda v: v, lambda v: html.escape(v, quote=False), html.escape):
                text = text.replace(encode(old_value), encode(new_value))
    return text


def fill_event(template, values, link, image_url):
    """(body, body_source) of a campaign made from the visual template for one event.

    ``values`` maps EVENT_TOKENS to text. Without an image, the event image block is dropped.
    """
    source = json.loads(template['body_source'])

    def fill(node):
        if isinstance(node, dict):
            return {k: fill(v) for k, v in node.items()}
        if isinstance(node, list):
            return [fill(v) for v in node]
        if isinstance(node, str):
            for token, value in values.items():
                node = node.replace(token, value)
            return node.replace(EVENT_IMAGE, image_url or EVENT_IMAGE).replace(EVENT_LINK, link)
        return node

    if not image_url:
        image_ids = [k for k, block in source.items() if k != 'root'
                     and block.get('type') == 'Image' and block['data'].get('props', {}).get('url') == EVENT_IMAGE]
        for key in image_ids:
            source.pop(key)
        root = source['root']['data']
        root['childrenIds'] = [i for i in root['childrenIds'] if i not in image_ids]
    source = fill(source)

    body = template['body']
    if image_url:
        body = body.replace(EVENT_IMAGE, html.escape(image_url))
    else:
        body = re.sub(r'<div[^>]*>\s*<img[^>]*%s[^>]*/?>\s*</div>' % re.escape(EVENT_IMAGE), '', body)
        body = re.sub(r'<img[^>]*%s[^>]*/?>' % re.escape(EVENT_IMAGE), '', body)
    body = body.replace(EVENT_LINK, html.escape(link))
    for token, value in values.items():
        escaped = html.escape(value)
        body = body.replace(token, escaped.replace('\n', '<br>') if token == '[event_description]' else escaped)
    return body, json.dumps(source, ensure_ascii=False)


def render(trigger_key, lang, brand):
    trigger = triggers.BY_KEY[trigger_key]
    return render_visual(trigger, lang, brand) if trigger.campaign else render_tx(trigger, lang, brand)
