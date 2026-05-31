from modeltranslation.translator import register, TranslationOptions
from .models import Role


@register(Role)
class RoleTranslationOptions(TranslationOptions):
    fields = ('name', 'description')
