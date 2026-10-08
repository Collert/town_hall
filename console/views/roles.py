from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _, gettext_lazy
from django.views.decorators.http import require_POST

from education.models import Skill, TrainingModule
from jobs.models import Role, RoleTrainingRequirement

from ..decorators import staff_required
from ..forms import RoleForm
from ..utils import ROLE_ICONS, ids_from, posted_ids, with_custom_icon

DIFFICULTY_LABELS = {
    0: gettext_lazy('Introductory'), 1: gettext_lazy('Basic'), 2: gettext_lazy('Intermediate'),
    3: gettext_lazy('Advanced'), 4: gettext_lazy('Expert'),
}


@staff_required
def role_list(request):
    roles = Role.objects.filter(system_key__isnull=True).annotate(
        module_count=Count('training_modules', filter=Q(roletrainingrequirement__mandatory=True), distinct=True),
        slot_count=Count('opportunities', distinct=True),
    ).prefetch_related('preferred_skills', 'venue').order_by('name')

    query = request.GET.get('q', '').strip()
    kind = request.GET.get('kind', '')
    difficulty = request.GET.get('difficulty', '')
    if query:
        roles = roles.filter(
            Q(name__icontains=query) | Q(description__icontains=query) | Q(preferred_skills__name__icontains=query)
        ).distinct()
    if kind == 'event':
        roles = roles.filter(permanent=False)
    elif kind == 'permanent':
        roles = roles.filter(permanent=True)

    levels = Role.complexity_levels()
    roles = list(roles)
    for role in roles:
        role.level = levels.get(role.pk, 0)
        role.level_label = DIFFICULTY_LABELS[role.level]
    if difficulty.isdigit():
        roles = [r for r in roles if r.level == int(difficulty)]

    context = {
        'roles': roles,
        'query': query,
        'kind': kind,
        'difficulty': difficulty,
        'difficulties': DIFFICULTY_LABELS.items(),
        'total_roles': Role.objects.filter(system_key__isnull=True).count(),
        'untrained_roles': Role.objects.filter(system_key__isnull=True, training_modules__isnull=True).count(),
    }
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'console/partials/role_grid.html', context)
    return render(request, 'console/role_list.html', context)


@staff_required
def role_edit(request, role_id=None):
    # Built-in roles (e.g. "Area lead") aren't in the directory and can't be edited here.
    role = get_object_or_404(Role, pk=role_id, system_key__isnull=True) if role_id else None

    if request.method == 'POST':
        form = RoleForm(with_custom_icon(request.POST), instance=role)
        module_ids = posted_ids(request, 'module_ids')
        skill_ids = posted_ids(request, 'skill_ids')
        if form.is_valid():
            with transaction.atomic():
                role = form.save()
                role.preferred_skills.set(Skill.objects.filter(pk__in=skill_ids))
                RoleTrainingRequirement.objects.filter(role=role).exclude(training_module_id__in=module_ids).delete()
                for module in TrainingModule.objects.filter(pk__in=module_ids):
                    RoleTrainingRequirement.objects.update_or_create(
                        role=role, training_module=module,
                        defaults={'mandatory': request.POST.get(f'mandatory_{module.pk}') == 'on'},
                    )
            messages.success(request, _('Role template "%(name)s" saved.') % {'name': role.name})
            return redirect('console_roles')
        messages.error(request, _('Please fix the highlighted fields.'))
        selected_skills = Skill.objects.filter(pk__in=skill_ids)
        requirements = [
            {'module': m, 'mandatory': request.POST.get(f'mandatory_{m.pk}') == 'on'}
            for m in TrainingModule.objects.filter(pk__in=module_ids)
        ]
    else:
        form = RoleForm(instance=role, initial={} if role else {'icon': ROLE_ICONS[0]})
        selected_skills = role.preferred_skills.all() if role else Skill.objects.none()
        requirements = [
            {'module': r.training_module, 'mandatory': r.mandatory}
            for r in RoleTrainingRequirement.objects.filter(role=role).select_related('training_module')
        ] if role else []

    selected_ids = [s.pk for s in selected_skills]
    suggestions = (Skill.objects.exclude(pk__in=selected_ids)
                   .annotate(uses=Count('preferred_for_roles', distinct=True) + Count('user_profiles', distinct=True))
                   .order_by('-uses', 'name')[:6])
    return render(request, 'console/role_edit.html', {
        'form': form,
        'role': role,
        'icons': ROLE_ICONS,
        'current_icon': form['icon'].value(),
        'selected_skills': selected_skills,
        'suggestions': suggestions,
        'requirements': requirements,
        'total_roles': Role.objects.filter(system_key__isnull=True).count(),
    })


@staff_required
@require_POST
def role_delete(request, role_id):
    role = get_object_or_404(Role, pk=role_id, system_key__isnull=True)  # built-in roles stay
    if role.opportunities.exists() or role.shifts.exists():
        messages.error(request, _('"%(name)s" is used by events or logged shifts, so it can\'t be deleted.') % {'name': role.name})
        return redirect('console_role_edit', role_id=role.pk)
    role.delete()
    messages.success(request, _('Role template deleted.'))
    return redirect('console_roles')


@staff_required
def module_search(request):
    query = request.GET.get('module_q', '').strip()
    exclude = ids_from(request.GET, 'module_ids')
    modules = TrainingModule.objects.exclude(pk__in=exclude).order_by('title')
    if query:
        modules = modules.filter(Q(title__icontains=query) | Q(description__icontains=query))
    return render(request, 'console/partials/module_search_results.html', {
        'modules': modules[:6] if query else modules[:4],
        'query': query,
    })


@staff_required
def module_row(request):
    module = get_object_or_404(TrainingModule, pk=request.GET.get('module_id'))
    return render(request, 'console/partials/module_requirement_row.html', {
        'req': {'module': module, 'mandatory': True},
    })
