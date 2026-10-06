from django.contrib import admin
from jobs.models import Role, RoleTrainingRequirement, Shift


class RoleTrainingRequirementInline(admin.TabularInline):
    model = RoleTrainingRequirement
    extra = 1


class RoleAdmin(admin.ModelAdmin):
    inlines = [RoleTrainingRequirementInline]


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    readonly_fields = ('start_time',)


admin.site.register(Role, RoleAdmin)