from django import template
from django.urls import translate_url

register = template.Library()


@register.simple_tag(takes_context=True)
def url_in_language(context, lang_code):
    """The current page's URL under another language prefix (/en/... -> /uk/...).

    Linking there switches language without going through set_language, whose redirect
    keeps the old URL whenever the language cookie disagrees with the page's prefix.
    """
    request = context['request']
    return translate_url(request.get_full_path(), lang_code)
