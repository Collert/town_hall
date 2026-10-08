from modeltranslation.translator import register, TranslationOptions
from .models import Event, EventArea


@register(Event)
class EventTranslationOptions(TranslationOptions):
    fields = ('title', 'description', 'location', 'post_event_statement')


@register(EventArea)
class EventAreaTranslationOptions(TranslationOptions):
    fields = ('name',)
