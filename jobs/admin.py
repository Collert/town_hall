from django.contrib import admin
from jobs.models import Role, RoleTrainingRequirement

class RoleTrainingRequirementInline(admin.TabularInline):
    model = RoleTrainingRequirement
    extra = 1

class RoleAdmin(admin.ModelAdmin):
    inlines = [RoleTrainingRequirementInline]

admin.site.register(Role, RoleAdmin)