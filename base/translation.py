from modeltranslation.translator import register, TranslationOptions
from .models import HeroSection, Venue, VenueFeature


@register(HeroSection)
class HeroSectionTranslationOptions(TranslationOptions):
    fields = ('title', 'subtitle', 'button_1_text', 'button_2_text')


@register(Venue)
class VenueTranslationOptions(TranslationOptions):
    fields = ('name', 'description')


@register(VenueFeature)
class VenueFeatureTranslationOptions(TranslationOptions):
    fields = ('name',)
