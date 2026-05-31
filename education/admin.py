from django.contrib import admin

from education.models import *


class QuizQuestionInline(admin.TabularInline):
    model = Quiz.questions.through
    extra = 1


class QuizAdmin(admin.ModelAdmin):
    inlines = [QuizQuestionInline]
    exclude = ('questions',)


class CertificateFileInline(admin.TabularInline):
    model = UserCertificationFile
    extra = 1

class UserCertificationAdmin(admin.ModelAdmin):
    inlines = [CertificateFileInline]

class ModuleCompletionInline(admin.TabularInline):
    model = TrainingModuleCompletion
    extra = 1

class TrainingModuleAdmin(admin.ModelAdmin):
    inlines = [ModuleCompletionInline]

admin.site.register(TrainingModule, TrainingModuleAdmin)
admin.site.register(Skill)
admin.site.register(TrainingLesson)
admin.site.register(Quiz, QuizAdmin)
admin.site.register(QuizQuestion)
admin.site.register(TrainingTopic)
admin.site.register(ExternalCertificate)
admin.site.register(UserCertification, UserCertificationAdmin)