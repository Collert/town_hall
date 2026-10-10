"""Training: modules and their lessons are read-only; quizzes can be added.

Lessons describe how this organization actually operates (its venues, its people, its
procedures), which only the people who work there know. An AI writing them would fill
the gaps with assumptions, so lessons stay with people. What an AI can do well is
check understanding of what people wrote: quiz questions must each come from a lesson
of the module, and say which one.
"""
from django.db import transaction
from django.db.models import Count, Max, Q

from education.models import Quiz, QuizQuestion, TrainingModule

from . import ToolError, array, boolean, get_or_error, integer, obj, set_translated, string, text, tool

GROUNDING = (' Lessons are written by the organization because only people who work there know how it really '
             'operates; they are read-only here.')

QUESTION = obj({
    'question': string('The question'),
    'lesson_id': integer('The lesson of this module the question is based on'),
    'options': array(string('An answer option', maxLength=200), 'Two to four answer options (not for true/false questions)',
                     minItems=2, maxItems=4),
    'correct_option': string('Letter of the correct option', enum=['A', 'B', 'C', 'D']),
    'true_false_answer': boolean('Makes this a true/false question with this answer; leave out options and correct_option'),
}, ['question', 'lesson_id'])


def _question(q):
    options = {letter: getattr(q, f'option_{letter.lower()}') for letter in 'ABCD'}
    return {
        'question': q.question_text,
        'options': {k: v for k, v in options.items() if v},
        'correct_option': q.correct_option,
        'true_false': q.is_true_false,
    }


@tool('list_training_modules', 'List training modules',
      'The training modules volunteers take, with which roles require them.' + GROUNDING,
      {'query': string('Optional filter on title or description')}, read_only=True)
def list_training_modules(ctx, query=''):
    modules = (TrainingModule.objects.annotate(lesson_count=Count('lessons', distinct=True), quiz_count=Count('quizzes', distinct=True))
               .prefetch_related('roletrainingrequirement_set__role').order_by('title'))
    for word in text(query).split():
        modules = modules.filter(Q(title__icontains=word) | Q(description__icontains=word))
    return [{
        'module_id': m.pk, 'title': m.title, 'description': m.description[:400], 'published': m.published,
        'lessons': m.lesson_count, 'quizzes': m.quiz_count, 'completions': m.completions.count(),
        'required_by_roles': [{'role': r.role.name, 'mandatory': r.mandatory} for r in m.roletrainingrequirement_set.all()],
    } for m in modules.distinct()] or 'No modules match.'


@tool('get_training_module', 'Read a training module',
      'A module\'s full content: every lesson\'s text and video link, and its quizzes with their questions and answers. '
      'Read it before writing quiz questions.' + GROUNDING,
      {'module_id': integer('The module')}, ['module_id'], read_only=True)
def get_training_module(ctx, module_id):
    module = get_or_error(TrainingModule.objects, module_id, 'training module')
    return {
        'module_id': module.pk, 'title': module.title, 'description': module.description,
        'published': module.published, 'expires_after_days': module.expires_after_days,
        'lessons': [{
            'lesson_id': l.pk, 'order': l.order, 'title': l.title, 'content': l.content,
            'video_url': l.video_url, 'video_minutes': l.video_length_minutes,
        } for l in module.lessons.order_by('order', 'pk')],
        'quizzes': [{
            'quiz_id': q.pk, 'order': q.order, 'title': q.title, 'pass_percentage': q.percentage_to_pass,
            'questions': [_question(x) for x in q.questions.order_by('pk')],
        } for q in module.quizzes.order_by('order', 'pk')],
    }


def _add_questions(module, quiz, questions):
    lesson_ids = set(module.lessons.values_list('pk', flat=True))
    built = []
    for index, item in enumerate(questions, start=1):
        if item['lesson_id'] not in lesson_ids:
            raise ToolError(f'Question {index}: lesson {item["lesson_id"]} is not part of this module. '
                            'Every question must come from one of its lessons.')
        q = QuizQuestion()
        set_translated(q, 'question_text', text(item['question']))
        if not q.question_text:
            raise ToolError(f'Question {index} is empty.')
        if item.get('true_false_answer') is not None:
            q.is_true_false = True
            q.correct_option = 'A' if item['true_false_answer'] else 'B'
            set_translated(q, 'option_a', 'True')
            set_translated(q, 'option_b', 'False')
        else:
            options = [text(o, 200) for o in item.get('options') or []]
            if len(options) < 2 or not all(options):
                raise ToolError(f'Question {index} needs two to four non-empty options, or a true_false_answer.')
            correct = item.get('correct_option')
            if not correct or 'ABCD'.index(correct) >= len(options):
                raise ToolError(f'Question {index}: correct_option must be one of the given options '
                                f'({", ".join("ABCD"[:len(options)])}).')
            q.correct_option = correct
            for letter, option in zip('abcd', options):
                set_translated(q, f'option_{letter}', option)
        built.append(q)
    for q in built:
        q.save()
        quiz.questions.add(q)
    return len(built)


@tool('create_quiz', 'Create a quiz',
      'Add a quiz to a training module. Each question must test something a lesson of the module actually says, and '
      'name that lesson_id; never test assumptions about how the organization works. Volunteers must pass every quiz '
      'of a module to complete it.' + GROUNDING,
      {
          'module_id': integer('The module'),
          'title': string('Quiz title, e.g. "Kitchen safety check"', maxLength=100),
          'pass_percentage': integer('Score needed to pass (default 70)', minimum=1),
          'questions': array(QUESTION, 'The questions', minItems=1, maxItems=30),
      }, ['module_id', 'title', 'questions'])
def create_quiz(ctx, module_id, title, questions, pass_percentage=70):
    module = get_or_error(TrainingModule.objects, module_id, 'training module')
    if pass_percentage > 100:
        raise ToolError('pass_percentage can be at most 100.')
    if not module.lessons.exists():
        raise ToolError('This module has no lessons yet, so there is nothing to base a quiz on.')
    with transaction.atomic():
        quiz = Quiz(training_module=module, percentage_to_pass=pass_percentage,
                    order=(module.quizzes.aggregate(m=Max('order'))['m'] or 0) + 1)
        set_translated(quiz, 'title', text(title, 100))
        quiz.save()
        count = _add_questions(module, quiz, questions)
    return {'quiz_id': quiz.pk, 'title': quiz.title, 'questions': count,
            'note': 'Staff can review and edit it in Town Hall under Training Modules.'}


@tool('add_quiz_questions', 'Add quiz questions',
      'Add questions to an existing quiz. Same rules as create_quiz: each question tests what a lesson of the module '
      'says and names that lesson_id.' + GROUNDING,
      {
          'quiz_id': integer('The quiz'),
          'questions': array(QUESTION, 'The questions', minItems=1, maxItems=30),
      }, ['quiz_id', 'questions'])
def add_quiz_questions(ctx, quiz_id, questions):
    quiz = get_or_error(Quiz.objects.select_related('training_module'), quiz_id, 'quiz')
    with transaction.atomic():
        count = _add_questions(quiz.training_module, quiz, questions)
    return {'quiz_id': quiz.pk, 'added': count, 'total_questions': quiz.questions.count()}
