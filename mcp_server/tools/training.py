"""Training: modules, their lessons, and quizzes.

Lessons describe how this organization actually operates (its venues, its people, its
procedures), which only the people who work there know. An AI writing them would fill
the gaps with assumptions, so it may only arrange what people told it:

1. The user's material is stored word for word as TrainingSources of the module.
2. The user approves a lesson plan (ModulePlan) where every lesson names its sources and
   nothing is left open.
3. Lessons are written one step at a time, in order. Each one quotes the material it is
   built from; every quote must appear there word for word, and every number and link in
   the lesson must appear in the cited material.

New modules start unpublished, so staff read them before volunteers do. Quiz questions
must each come from a lesson of the module, and say which one.
"""
import re

from django.db import transaction
from django.db.models import Count, Max, Q

from education.models import Quiz, QuizQuestion, TrainingLesson, TrainingModule

from ..models import ModulePlan, TrainingSource
from . import ToolError, array, boolean, get_or_error, integer, obj, set_translated, string, text, tool

GROUNDING = (' Lessons may only contain what the organization told you, because only its people know how it really '
             'operates.')

WORKFLOW = (
    ' Writing training follows fixed steps, and lessons may contain nothing the organization did not tell you. '
    '1) Before creating anything, ask the user for the material and keep asking until you understand the whole '
    'subject: who the training is for, what they must be able to do afterwards, and the exact procedures, places, '
    'people, times, equipment and safety rules involved. Never fill a gap yourself. '
    '2) create_training_module. 3) add_training_source for every piece of material, word for word. '
    '4) Draft the lesson plan, show it to the user, ask about everything the material leaves open, store the answers '
    'with add_training_source, and save the approved plan with plan_training_module. '
    '5) write_lesson for each step in order, one at a time, re-reading that step\'s sources first (read_training_sources). '
    'Never add facts, steps, numbers, names, examples or advice that are not in the sources, not even common-sense '
    'best practice; you may only organise, shorten, format and plainly reword what they say.'
)

QUOTE = obj({
    'source_id': integer('A source of this module (from add_training_source) the quote comes from'),
    'lesson_id': integer('Or a lesson of this module the quote comes from'),
    'quote': string('A passage copied word for word from that source or lesson', maxLength=2000),
}, ['quote'])

QUOTES = array(QUOTE, 'Passages from the material this lesson is built from, copied word for word, covering everything '
               'the lesson says. Each names its source_id or lesson_id. Every number and link in the lesson must appear '
               'in the cited material', minItems=1, maxItems=60)

LESSON_FIELDS = {
    'title': string('Lesson title', maxLength=100),
    'content': string('The lesson in Markdown, built only from the quoted material'),
    'video_url': string('A video link that appears in the cited material (optional)', maxLength=200),
}

PUNCTUATION = str.maketrans({'‘': "'", '’': "'", '“': '"', '”': '"', '–': '-', '—': '-', ' ': ' '})
URL = re.compile(r'https?://[^\s)\]>"\']+')
LIST_MARKER = re.compile(r'^\s*\d+[.)]\s', re.M)
NUMBER = re.compile(r'\d+(?:[.,:/]\d+)*')
MIN_QUOTE = 15

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
      'A module\'s full content: every lesson\'s text and video link, its quizzes with their questions and answers, the '
      'material stored for it and its lesson plan. Read it before writing quiz questions or lessons.' + GROUNDING,
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
        'sources': [{'source_id': s.pk, 'title': s.title, 'origin': s.origin, 'characters': len(s.content)}
                    for s in module.ai_sources.order_by('pk')],
        'plan': _plan(module),
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


# ---------------------------------------------------------------------------
# Writing modules and lessons
# ---------------------------------------------------------------------------

def _writable_module(module_id):
    module = get_or_error(TrainingModule.objects, module_id, 'training module')
    if module.system_key:
        raise ToolError('Town Hall maintains this module itself; it can\'t be changed here.')
    return module


def _plan(module):
    plan = ModulePlan.objects.filter(module=module).first()
    if plan is None:
        return None
    return {'goal': plan.goal, 'steps': [{'step': n, **step} for n, step in enumerate(plan.steps, start=1)]}


def _next_step(plan):
    return next((n for n, step in enumerate(plan.steps, start=1) if not step['written']), None)


def _normalize(value):
    """Lowercase, one space between words, plain quotes and dashes, no Markdown emphasis."""
    value = re.sub(r'[*_#>`]', '', (value or '').translate(PUNCTUATION).casefold())
    return ' '.join(value.split())


def _check_grounding(module, quotes, written, current=None):
    """Raise ToolError unless every quote is in the material it cites, and every number and
    link in ``written`` (the new text) appears in that material. ``current`` is the lesson
    being edited, which is checked against what it said before the change."""
    sources = {s.pk: s.content for s in module.ai_sources.all()}
    lessons = {l.pk: l.content for l in module.lessons.all()}
    if current is not None:
        lessons[current.pk] = current.content
    cited = {}
    for index, item in enumerate(quotes, start=1):
        source_id, lesson_id = item.get('source_id'), item.get('lesson_id')
        if (source_id is None) == (lesson_id is None):
            raise ToolError(f'Quote {index} needs either a source_id or a lesson_id.')
        if source_id is not None:
            if source_id not in sources:
                raise ToolError(f'Quote {index}: {source_id} is not a source of this module.')
            label, body = f'source {source_id}', sources[source_id]
        else:
            if lesson_id not in lessons:
                raise ToolError(f'Quote {index}: {lesson_id} is not a lesson of this module.')
            label, body = f'lesson {lesson_id}', lessons[lesson_id]
        quote = _normalize(item['quote'])
        if len(quote) < MIN_QUOTE:
            raise ToolError(f'Quote {index} is too short to show where the lesson comes from; quote at least a full phrase.')
        if quote not in _normalize(body):
            raise ToolError(f'Quote {index} does not appear word for word in {label}. Copy passages exactly. If the lesson '
                            'needs something the material does not say, stop and ask the user, then store their answer '
                            'with add_training_source.')
        cited[label] = body

    material = '\n'.join(cited.values())
    written = '\n'.join(filter(None, written))
    links = [u.rstrip('.,;:') for u in URL.findall(written)]
    missing = [u for u in links if _normalize(u) not in _normalize(material)]
    if missing:
        raise ToolError(f'These links are not in the cited material: {", ".join(missing)}. Use only links the user gave '
                        'you, and quote the passage that contains them.')
    numbers = NUMBER.findall(URL.sub(' ', LIST_MARKER.sub(' ', written)))
    missing = sorted({n for n in numbers if not re.search(rf'(?<!\d){re.escape(n)}(?!\d)', material)})
    if missing:
        raise ToolError(f'These numbers are not in the cited material: {", ".join(missing)}. Numbers (times, amounts, '
                        'phone numbers, limits) must come from the user: quote the passage that states them, or ask the '
                        'user and store their answer with add_training_source.')


def _save_lesson(lesson, title, content, video_url):
    set_translated(lesson, 'title', title)
    set_translated(lesson, 'content', content)
    if video_url != lesson.video_url:
        lesson.video_url, lesson.video_length_minutes = video_url, None
    lesson.save()


@tool('create_training_module', 'Create a training module',
      'Start a new, unpublished training module; staff review and publish it in Town Hall. Only call this once the user '
      'has given you the material to teach.' + WORKFLOW,
      {
          'title': string('Module title', maxLength=100),
          'description': string('What the module covers and who it is for, as the user described it'),
          'icon': string('A Google Material Symbols icon name, e.g. restaurant'),
          'expires_after_days': integer('Volunteers must retake it after this many days (only if the user said so)', minimum=1),
      }, ['title', 'description', 'icon'])
def create_training_module(ctx, title, description, icon, expires_after_days=None):
    module = TrainingModule(icon=text(icon, 50), expires_after_days=expires_after_days, published=False)
    set_translated(module, 'title', text(title, 100))
    set_translated(module, 'description', text(description))
    if not module.title or not module.description:
        raise ToolError('title and description can\'t be empty.')
    module.save()
    return {'module_id': module.pk, 'title': module.title, 'published': False,
            'next': 'Store the user\'s material with add_training_source, word for word.'}


@tool('add_training_source', 'Store training material',
      'Store material the user gave you for a module, exactly as they gave it: text they typed or pasted, the text of a '
      'document they shared, or their answer to one of your questions (in their words). Lessons can only quote what is '
      'stored here or what the module\'s lessons already say. Never paraphrase, summarise, complete or write source '
      'material yourself.' + WORKFLOW,
      {
          'module_id': integer('The module'),
          'title': string('A short label, e.g. "Kitchen manager\'s notes" or "Answer: where the first-aid kit is"', maxLength=200),
          'origin': string('Where it came from', enum=[o for o, _ in TrainingSource.ORIGINS]),
          'content': string('The material, word for word', maxLength=100000),
      }, ['module_id', 'title', 'origin', 'content'])
def add_training_source(ctx, module_id, title, origin, content):
    module = _writable_module(module_id)
    content = (content or '').strip()
    if not content:
        raise ToolError('content is empty.')
    source = TrainingSource.objects.create(module=module, title=text(title, 200) or 'Material', origin=origin,
                                           content=content, added_by=ctx.user)
    return {'source_id': source.pk, 'title': source.title, 'characters': len(content),
            'sources_for_module': module.ai_sources.count()}


@tool('read_training_sources', 'Read training material',
      'The full text of the material stored for a module. Re-read a step\'s sources right before writing its lesson.',
      {
          'module_id': integer('The module'),
          'source_ids': array(integer('Source id'), 'Only these sources (default: all)'),
      }, ['module_id'], read_only=True)
def read_training_sources(ctx, module_id, source_ids=None):
    module = get_or_error(TrainingModule.objects, module_id, 'training module')
    sources = module.ai_sources.order_by('pk')
    if source_ids:
        sources = sources.filter(pk__in=source_ids)
    return [{'source_id': s.pk, 'title': s.title, 'origin': s.get_origin_display(), 'content': s.content}
            for s in sources] or 'No material is stored for this module yet; ask the user for it.'


@tool('plan_training_module', 'Save a lesson plan',
      'Save the lesson plan the user approved, replacing any earlier plan. Show the plan to the user first and ask about '
      'everything the material leaves open; a plan with gaps is not saved. Lessons already written keep their place '
      'when you pass their lesson_id; to rewrite one, use update_training_lesson.' + WORKFLOW,
      {
          'module_id': integer('The module'),
          'goal': string('What volunteers will be able to do after the module, as the user put it'),
          'lessons': array(obj({
              'title': string('Lesson title', maxLength=100),
              'teaches': string('What this lesson covers, from the sources'),
              'source_ids': array(integer('Source id'), 'The stored sources this lesson is built from', minItems=1),
              'lesson_id': integer('An existing lesson of this module this step rewrites (optional)'),
          }, ['title', 'teaches', 'source_ids']), 'The lessons, in order', minItems=1, maxItems=20),
          'gaps': array(string('Something a lesson needs that the material does not say'),
                        'Everything the lessons would need that the stored material does not cover. It must be empty: '
                        'ask the user about each gap and store the answers first'),
          'confirmed_with_user': boolean('The user has seen and approved this exact plan'),
      }, ['module_id', 'goal', 'lessons', 'gaps', 'confirmed_with_user'])
def plan_training_module(ctx, module_id, goal, lessons, gaps, confirmed_with_user):
    module = _writable_module(module_id)
    gaps = [g for g in (text(g) for g in gaps) if g]
    if gaps:
        raise ToolError('The plan was not saved because the material leaves these open: ' + '; '.join(gaps) + '. Ask the '
                        'user about each one, store their answers with add_training_source, then plan again.')
    if not confirmed_with_user:
        raise ToolError('Show the plan to the user and get their approval before saving it.')
    source_ids = set(module.ai_sources.values_list('pk', flat=True))
    if not source_ids:
        raise ToolError('No material is stored for this module yet. Ask the user for it and store it with add_training_source.')
    lesson_ids = set(module.lessons.values_list('pk', flat=True))
    old = ModulePlan.objects.filter(module=module).first()
    written = {s['lesson_id'] for s in old.steps if s['written']} if old else set()
    steps, seen = [], set()
    for index, item in enumerate(lessons, start=1):
        unknown = set(item['source_ids']) - source_ids
        if unknown:
            raise ToolError(f'Lesson {index}: {", ".join(map(str, sorted(unknown)))} are not sources of this module.')
        lesson_id = item.get('lesson_id')
        if lesson_id is not None and (lesson_id not in lesson_ids or lesson_id in seen):
            raise ToolError(f'Lesson {index}: {lesson_id} is not a lesson of this module, or is already in the plan.')
        seen.add(lesson_id)
        title, teaches = text(item['title'], 100), text(item['teaches'])
        if not title or not teaches:
            raise ToolError(f'Lesson {index} needs a title and what it teaches.')
        steps.append({'title': title, 'teaches': teaches, 'source_ids': sorted(set(item['source_ids'])),
                      'lesson_id': lesson_id, 'written': lesson_id is not None and lesson_id in written})
    plan, _ = ModulePlan.objects.update_or_create(module=module, defaults={
        'goal': text(goal), 'steps': steps, 'approved_by': ctx.user,
    })
    step = _next_step(plan)
    return {'plan': _plan(module), 'next': (f'Re-read the sources of step {step} with read_training_sources, then write it '
                                            'with write_lesson.') if step else 'Every lesson in the plan is written.'}


@tool('write_lesson', 'Write the next planned lesson',
      'Write the next lesson of the module\'s approved plan. Lessons are written one at a time, in plan order. Use only '
      'what the quoted material says: organise, shorten, format and plainly reword it, but add nothing (no extra facts, '
      'steps, numbers, names, examples, tips or general advice). If the lesson needs something the material does not '
      'say, stop and ask the user instead.' + WORKFLOW,
      {
          'module_id': integer('The module'),
          'step': integer('The plan step being written (must be the next unwritten one)', minimum=1),
          **LESSON_FIELDS,
          'quotes': QUOTES,
      }, ['module_id', 'step', 'content', 'quotes'])
def write_lesson(ctx, module_id, step, content, quotes, title=None, video_url=None):
    module = _writable_module(module_id)
    plan = ModulePlan.objects.filter(module=module).first()
    if plan is None:
        raise ToolError('This module has no approved lesson plan yet. Plan it with the user and save it with plan_training_module.')
    expected = _next_step(plan)
    if expected is None:
        raise ToolError('Every lesson in the plan is written. Change a lesson with update_training_lesson, or plan again.')
    if step != expected:
        raise ToolError(f'Lessons are written one at a time, in order: the next one is step {expected}.')
    planned = plan.steps[step - 1]
    title = text(title, 100) or planned['title']
    content, video_url = text(content), text(video_url) or None
    if not content:
        raise ToolError('content is empty.')
    lesson = module.lessons.filter(pk=planned['lesson_id']).first() if planned['lesson_id'] else None
    _check_grounding(module, quotes, [title, content, video_url], current=lesson)
    if lesson is None:
        lesson = TrainingLesson(training_module=module, order=(module.lessons.aggregate(m=Max('order'))['m'] or 0) + 1)
    _save_lesson(lesson, title, content, video_url)
    planned.update(lesson_id=lesson.pk, written=True)
    plan.save(update_fields=['steps', 'updated_at'])
    result = {'lesson_id': lesson.pk, 'title': lesson.title, 'step': step, 'of': len(plan.steps)}
    following = _next_step(plan)
    if following:
        result['next'] = {'step': following, **plan.steps[following - 1],
                          'do': 'Re-read these sources with read_training_sources before writing it.'}
    elif module.published:
        result['next'] = 'Every lesson is written.'
    else:
        result['next'] = ('Every lesson is written. Offer a quiz built from them (create_quiz), and ask the user to '
                          'review the module and publish it in Town Hall.')
    return result


@tool('update_training_lesson', 'Edit a lesson',
      'Change a lesson\'s title, text or video link. The new version may only contain what the quoted material says: '
      'quote the lesson itself (lesson_id) for what it already said, and stored sources (source_id) for anything new. '
      'To add information that neither has, ask the user and store their answer with add_training_source first. '
      'Changes to a published module reach volunteers at once; translations in other languages are left as they were.'
      + GROUNDING,
      {'lesson_id': integer('The lesson'), **LESSON_FIELDS, 'quotes': QUOTES},
      ['lesson_id', 'quotes'])
def update_training_lesson(ctx, lesson_id, quotes, title=None, content=None, video_url=None):
    lesson = get_or_error(TrainingLesson.objects, lesson_id, 'lesson')
    module = _writable_module(lesson.training_module_id)
    if title is None and content is None and video_url is None:
        raise ToolError('Give a new title, content or video_url.')
    new_title = text(title, 100) or lesson.title
    new_content = text(content) or lesson.content
    new_video = (text(video_url) or None) if video_url is not None else lesson.video_url
    changed = [new_title if title is not None else None, new_content if content is not None else None,
               new_video if video_url is not None else None]
    _check_grounding(module, quotes, changed, current=lesson)
    _save_lesson(lesson, new_title, new_content, new_video)
    return {'lesson_id': lesson.pk, 'title': lesson.title, 'module_published': module.published,
            'note': 'Translations in other languages were not changed; staff can refresh them in Town Hall.'}
