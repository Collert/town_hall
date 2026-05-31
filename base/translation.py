from modeltranslation.translator import register, TranslationOptions
from .models import HeroSection


@register(HeroSection)
class HeroSectionTranslationOptions(TranslationOptions):
    fields = ('title', 'subtitle', 'button_1_text', 'button_2_text')