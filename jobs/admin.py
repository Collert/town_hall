from django.contrib import admin
from jobs.models import Role, RoleCertificateRequirement, RoleTrainingRequirement, Shift


class RoleCertificateRequirementInline(admin.TabularInline):
    model = RoleCertificateRequirement
    extra = 1


class RoleTrainingRequirementInline(admin.TabularInline):
    model = RoleTrainingRequirement
    extra = 1


class RoleAdmin(admin.ModelAdmin):
    inlines = [RoleTrainingRequirementInline, RoleCertificateRequirementInline]


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    readonly_fields = ('start_time',)


admin.site.register(Role, RoleAdmin)