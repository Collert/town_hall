from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from education.models import Quiz, QuizQuestion, Skill, TrainingLesson, TrainingModule, TrainingTopic

from ..decorators import staff_required
from ..forms import LessonForm, QuizForm, QuizQuestionForm, TrainingModuleForm
from ..utils import MODULE_ICONS, posted_ids, with_custom_icon


def _structure(module, current=None):
    """Ordered lessons then quizzes, as shown in the editor sidebar.

    Items can only be reordered among their own kind, so each carries
    first/last flags for its group.
    """
    groups = [
        ('lesson', list(module.lessons.order_by('order', 'pk'))),
        ('quiz', list(module.quizzes.annotate(question_count=Count('questions')).order_by('order', 'pk'))),
    ]
    items = []
    for kind, objects in groups:
        for index, obj in enumerate(objects):
            items.append({
                'kind': kind, 'obj': obj,
                'current': current is not None and type(current) is type(obj) and current.pk == obj.pk,
                'first': index == 0, 'last': index == len(objects) - 1,
            })
    return items


def _structure_context(module, current=None):
    current_kind = 'quiz' if isinstance(current, Quiz) else 'lesson' if current else ''
    return {'module': module, 'structure': _structure(module, current), 'current': current, 'current_kind': current_kind}


@staff_required
def module_list(request):
    modules = TrainingModule.objects.annotate(
        lesson_count=Count('lessons', distinct=True),
        quiz_count=Count('quizzes', distinct=True),
        completion_count=Count('completions', distinct=True),
        role_count=Count('roles', distinct=True),
    ).select_related('topic').order_by('-published', 'title')
    query = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    if query:
        modules = modules.filter(Q(title__icontains=query) | Q(description__icontains=query) | Q(topic__name__icontains=query))
    if status == 'published':
        modules = modules.filter(published=True)
    elif status == 'draft':
        modules = modules.filter(published=False)
    context = {
        'modules': modules,
        'query': query,
        'status': status,
        'published_count': TrainingModule.objects.filter(published=True).count(),
        'draft_count': TrainingModule.objects.filter(published=False).count(),
    }
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'console/partials/module_grid.html', context)
    return render(request, 'console/module_list.html', context)


@staff_required
def module_edit(request, module_id=None):
    module = get_object_or_404(TrainingModule, pk=module_id) if module_id else None

    if request.method == 'POST':
        form = TrainingModuleForm(with_custom_icon(request.POST), request.FILES, instance=module)
        skill_ids = posted_ids(request, 'skill_ids')
        if form.is_valid():
            with transaction.atomic():
                module = form.save(commit=False)
                new_topic = request.POST.get('new_topic', '').strip()
                if new_topic:
                    module.topic = TrainingTopic.objects.filter(name__iexact=new_topic).first() \
                        or TrainingTopic.objects.create(name=new_topic)
                module.save()
                module.skills.set(Skill.objects.filter(pk__in=skill_ids))
            if module.published and not (module.lessons.exists() or module.quizzes.exists()):
                messages.warning(request, _('Module saved and published, but it has no lessons or quizzes yet.'))
            else:
                messages.success(request, _('Module saved.'))
            return redirect('console_module_edit', module_id=module.pk)
        messages.error(request, _('Please fix the highlighted fields.'))
        selected_skills = Skill.objects.filter(pk__in=skill_ids)
    else:
        form = TrainingModuleForm(instance=module, initial={} if module else {'icon': MODULE_ICONS[0]})
        selected_skills = module.skills.all() if module else Skill.objects.none()

    context = {
        'form': form,
        'module': module,
        'icons': MODULE_ICONS,
        'current_icon': form['icon'].value(),
        'selected_skills': selected_skills,
    }
    if module:
        context.update(_structure_context(module))
        context.update({
            'total_length': module.get_total_length(),
            'pass_scores': sorted({q.percentage_to_pass for q in module.quizzes.all()}),
            'completion_count': module.completions.count(),
            'started_count': module.started_by.count(),
            'role_count': module.roles.count(),
        })
    return render(request, 'console/module_edit.html', context)


@staff_required
@require_POST
def module_delete(request, module_id):
    module = get_object_or_404(TrainingModule, pk=module_id)
    if module.completions.exists():
        messages.error(request, _('Volunteers have completed this module, so it can only be unpublished, not deleted.'))
        return redirect('console_module_edit', module_id=module.pk)
    module.delete()
    messages.success(request, _('Module deleted.'))
    return redirect('console_modules')


@staff_required
@require_POST
def move_item(request, module_id, kind, item_id, direction):
    module = get_object_or_404(TrainingModule, pk=module_id)
    if kind == 'lesson':
        items = list(module.lessons.order_by('order', 'pk'))
    elif kind == 'quiz':
        items = list(module.quizzes.order_by('order', 'pk'))
    else:
        raise Http404
    index = next((i for i, item in enumerate(items) if item.pk == item_id), None)
    if index is None:
        raise Http404
    target = index - 1 if direction == 'up' else index + 1
    if 0 <= target < len(items):
        items[index], items[target] = items[target], items[index]
        for position, item in enumerate(items):
            if item.order != position:
                item.order = position
                type(item).objects.filter(pk=item.pk).update(order=position)
    current = None
    if request.POST.get('current_kind') == 'lesson':
        current = module.lessons.filter(pk=request.POST.get('current_id')).first()
    elif request.POST.get('current_kind') == 'quiz':
        current = module.quizzes.filter(pk=request.POST.get('current_id')).first()
    return render(request, 'console/partials/module_structure.html', _structure_context(module, current))


@staff_required
def lesson_edit(request, module_id, lesson_id=None):
    module = get_object_or_404(TrainingModule, pk=module_id)
    lesson = get_object_or_404(TrainingLesson, pk=lesson_id, training_module=module) if lesson_id else None

    if request.method == 'POST':
        form = LessonForm(request.POST, instance=lesson)
        if form.is_valid():
            lesson = form.save(commit=False)
            if not lesson.pk:
                lesson.training_module = module
                lesson.order = (module.lessons.aggregate(m=Max('order'))['m'] or 0) + 1
            lesson.save()
            messages.success(request, _('Lesson saved.'))
            if request.POST.get('next') == 'new':
                return redirect('console_lesson_create', module_id=module.pk)
            return redirect('console_lesson_edit', module_id=module.pk, lesson_id=lesson.pk)
        messages.error(request, _('Please fix the highlighted fields.'))
    else:
        form = LessonForm(instance=lesson)

    return render(request, 'console/lesson_edit.html', {
        **_structure_context(module, lesson),
        'form': form,
        'lesson': lesson,
    })


@staff_required
@require_POST
def lesson_delete(request, module_id, lesson_id):
    lesson = get_object_or_404(TrainingLesson, pk=lesson_id, training_module_id=module_id)
    lesson.delete()
    messages.success(request, _('Lesson deleted.'))
    return redirect('console_module_edit', module_id=module_id)


@staff_required
@require_POST
def quiz_create(request, module_id):
    module = get_object_or_404(TrainingModule, pk=module_id)
    quiz = Quiz.objects.create(
        training_module=module,
        title_en='Knowledge Check',
        order=(module.quizzes.aggregate(m=Max('order'))['m'] or 0) + 1,
    )
    messages.success(request, _('Quiz created. Add your first question below.'))
    url = reverse('console_quiz_edit', args=[module.pk, quiz.pk])
    if request.headers.get('HX-Request') == 'true':
        return HttpResponse(headers={'HX-Redirect': url})
    return redirect(url)


def _quiz_context(module, quiz, question_form=None, editing=None):
    return {
        **_structure_context(module, quiz),
        'quiz': quiz,
        'questions': quiz.questions.order_by('pk'),
        'question_form': question_form or QuizQuestionForm(initial={'correct_option': 'A'}),
        'editing': editing,
    }


@staff_required
def quiz_edit(request, module_id, quiz_id):
    module = get_object_or_404(TrainingModule, pk=module_id)
    quiz = get_object_or_404(Quiz, pk=quiz_id, training_module=module)

    if request.method == 'POST':
        form = QuizForm(request.POST, instance=quiz)
        if form.is_valid():
            form.save()
            messages.success(request, _('Quiz settings saved.'))
            return redirect('console_quiz_edit', module_id=module.pk, quiz_id=quiz.pk)
        messages.error(request, _('Please fix the highlighted fields.'))
    else:
        form = QuizForm(instance=quiz)

    return render(request, 'console/quiz_edit.html', {**_quiz_context(module, quiz), 'form': form})


@staff_required
def question_save(request, module_id, quiz_id, question_id=None):
    module = get_object_or_404(TrainingModule, pk=module_id)
    quiz = get_object_or_404(Quiz, pk=quiz_id, training_module=module)
    question = get_object_or_404(QuizQuestion, pk=question_id, quizzes=quiz) if question_id else None

    if request.method == 'GET':
        # Load a question into the composer for editing.
        return render(request, 'console/partials/quiz_questions.html',
                      _quiz_context(module, quiz, QuizQuestionForm(instance=question), question))

    form = QuizQuestionForm(request.POST, instance=question)
    if form.is_valid():
        saved = form.save()
        quiz.questions.add(saved)
        messages.success(request, _('Question updated.') if question else _('Question added to the quiz.'))
        return render(request, 'console/partials/quiz_questions.html', _quiz_context(module, quiz))
    return render(request, 'console/partials/quiz_questions.html', _quiz_context(module, quiz, form, question))


@staff_required
@require_POST
def question_delete(request, module_id, quiz_id, question_id):
    module = get_object_or_404(TrainingModule, pk=module_id)
    quiz = get_object_or_404(Quiz, pk=quiz_id, training_module=module)
    question = get_object_or_404(QuizQuestion, pk=question_id, quizzes=quiz)
    quiz.questions.remove(question)
    if not question.quizzes.exists():
        question.delete()
    messages.success(request, _('Question removed.'))
    return render(request, 'console/partials/quiz_questions.html', _quiz_context(module, quiz))


@staff_required
@require_POST
def quiz_delete(request, module_id, quiz_id):
    quiz = get_object_or_404(Quiz, pk=quiz_id, training_module_id=module_id)
    for question in quiz.questions.all():
        if question.quizzes.count() == 1:
            question.delete()
    quiz.delete()
    messages.success(request, _('Quiz deleted.'))
    return redirect('console_module_edit', module_id=module_id)


@staff_required
@require_POST
def markdown_preview(request):
    from events.templatetags.md_extras import render_markdown
    source = request.POST.get(request.POST.get('field', ''), '')
    if not source.strip():
        return HttpResponse('<p class="c-muted">%s</p>' % _('Nothing to preview yet.'))
    return HttpResponse(render_markdown(source))
