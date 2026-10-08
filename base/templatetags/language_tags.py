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


# Language code -> flag-icons country code, where they differ (English is shown with Canada's flag).
FLAG_COUNTRIES = {'en': 'ca', 'uk': 'ua', 'zh-hant': 'hk', 'tl': 'ph'}


@register.filter
def lang_flag(lang_code):
    """flag-icons class for a language: {{ code|lang_flag }} -> 'fi fi-ua'."""
    return f'fi fi-{FLAG_COUNTRIES.get(lang_code, lang_code)}'
