from modeltranslation.translator import register, TranslationOptions
from .models import TrainingModule, Skill, TrainingLesson, Quiz, QuizQuestion, TrainingTopic


@register(TrainingModule)
class TrainingModuleTranslationOptions(TranslationOptions):
    fields = ('title', 'description')


@register(Skill)
class SkillTranslationOptions(TranslationOptions):
    fields = ('name',)


@register(TrainingTopic)
class TrainingTopicTranslationOptions(TranslationOptions):
    fields = ('name',)

@register(TrainingLesson)
class TrainingLessonTranslationOptions(TranslationOptions):
    fields = ('title', 'content')


@register(Quiz)
class QuizTranslationOptions(TranslationOptions):
    fields = ('title',)


@register(QuizQuestion)
class QuizQuestionTranslationOptions(TranslationOptions):
    fields = ('question_text', 'option_a', 'option_b', 'option_c', 'option_d')
