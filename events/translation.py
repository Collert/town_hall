from modeltranslation.translator import register, TranslationOptions
from .models import Event


@register(Event)
class EventTranslationOptions(TranslationOptions):
    fields = ('title', 'description', 'location', 'post_event_statement')
