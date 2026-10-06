from django.contrib import messages
from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .. import forms
from ..auto_translate import TranslationError, translate_texts
from ..decorators import staff_required
from ..utils import LANGUAGE_CODES

TRANSLATABLE_FORMS = {
    form.translation_key: form
    for form in (forms.EventForm, forms.RoleForm, forms.TrainingModuleForm,
                 forms.LessonForm, forms.QuizForm, forms.QuizQuestionForm, forms.VenueForm, forms.HeroSectionForm)
}


@staff_required
@require_POST
def auto_translate(request):
    """HTMX: fill empty translations from the default language, swapping the fields in out-of-band.

    Existing translations are never overwritten.
    """
    form_class = TRANSLATABLE_FORMS.get(request.POST.get('form_key'))
    if not form_class:
        return HttpResponseBadRequest()

    source, targets = LANGUAGE_CODES[0], LANGUAGE_CODES[1:]
    data = request.POST.copy()
    filled = []
    failed = False
    try:
        for target in targets:
            names = [
                name for name in form_class.translated
                if data.get(f'{name}_{source}', '').strip() and not data.get(f'{name}_{target}', '').strip()
            ]
            translations = translate_texts([data[f'{name}_{source}'] for name in names], source, target)
            for name, text in zip(names, translations):
                data[f'{name}_{target}'] = text
                filled.append(f'{name}_{target}')
    except TranslationError as exc:
        failed = True
        messages.error(request, _('Auto-translate failed: %(error)s') % {'error': exc})

    if filled:
        messages.success(request, _('Filled %(count)d translation(s). Review them before saving.') % {'count': len(filled)})
    elif not failed:
        messages.info(request, _('Nothing to translate: fill in the first language, or every language already has text.'))

    form = form_class(data=data)
    return render(request, 'console/partials/translated_oob.html', {
        'fields': [form[name] for name in filled],
        'md_fields': request.POST.get('md_fields', ''),
    })
