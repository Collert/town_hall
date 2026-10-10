"""Role templates: the directory of roles events are staffed with, and the existing
training modules each role requires. Permanent staff positions and built-in roles are
read-only here, and roles are never deleted."""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from education.models import Skill, TrainingModule
from events.models import EventRoleSlot
from jobs.models import Role, RoleTrainingRequirement

from . import ToolError, array, boolean, get_or_error, integer, obj, set_translated, string, text, tool

TRAINING = obj({
    'module_id': integer('A training module (from list_training_modules)'),
    'mandatory': boolean('Volunteers must finish it before signing up (default true); false makes it recommended'),
}, ['module_id'])

ROLE_FIELDS = {
    'name': string('At most 50 characters, e.g. "Parking attendant"', maxLength=50),
    'description': string('What the volunteer does, in a sentence or two'),
    'icon': string('A Google Material Symbols icon name, e.g. local_parking'),
    'training': array(TRAINING, 'Training modules for the role. On update this replaces the whole list'),
    'preferred_skill_ids': array(integer('Skill id'), 'Skills that make someone a good fit (from list_skills). On update this replaces the list'),
}


def _history(role_ids):
    """{role_id: usage at past events}, to help judge how many of a role an event needs."""
    stats = {}
    slots = (EventRoleSlot.objects.filter(role_id__in=role_ids, event__end_date__lt=timezone.now())
             .select_related('event').values('role_id', 'event_id', 'event__attendees', 'required_qty'))
    per_event = {}
    for row in slots:
        key = (row['role_id'], row['event_id'])
        entry = per_event.setdefault(key, {'required': 0, 'attendees': row['event__attendees']})
        entry['required'] += row['required_qty']
    for (role_id, _event_id), entry in per_event.items():
        s = stats.setdefault(role_id, {'past_events': 0, 'required_total': 0, 'per_100': []})
        s['past_events'] += 1
        s['required_total'] += entry['required']
        if entry['attendees']:
            s['per_100'].append(entry['required'] / entry['attendees'] * 100)
    return {
        role_id: {
            'past_events': s['past_events'],
            'average_needed_per_event': round(s['required_total'] / s['past_events'], 1),
            'average_needed_per_100_attendees': round(sum(s['per_100']) / len(s['per_100']), 1) if s['per_100'] else None,
        } for role_id, s in stats.items()
    }


def role_details(role, history=None):
    return {
        'role_id': role.pk, 'name': role.name, 'description': role.description, 'icon': role.icon,
        'training': [
            {'module_id': r.training_module_id, 'title': r.training_module.title, 'mandatory': r.mandatory}
            for r in role.roletrainingrequirement_set.all()
        ],
        'preferred_skills': [s.name for s in role.preferred_skills.all()],
        'history': history,
    }


@tool('list_roles', 'List roles',
      'The role directory: event roles with their training requirements, preferred skills, and how many were needed '
      'at past events (on average and per 100 attendees), to judge how many of each role a new event needs.',
      {'query': string('Optional filter on name or description')}, read_only=True)
def list_roles(ctx, query=''):
    roles = (Role.objects.filter(system_key__isnull=True, permanent=False)
             .prefetch_related('roletrainingrequirement_set__training_module', 'preferred_skills').order_by('name'))
    for word in text(query).split():
        roles = roles.filter(Q(name__icontains=word) | Q(description__icontains=word))
    roles = list(roles.distinct())
    history = _history([r.pk for r in roles])
    return [role_details(r, history.get(r.pk)) for r in roles] or 'No roles match.'


@tool('list_skills', 'List skills', 'Skills volunteers list on their profiles, for a role\'s preferred skills.', read_only=True)
def list_skills(ctx):
    return [{'skill_id': s.pk, 'name': s.name} for s in Skill.objects.order_by('name')] or 'No skills yet.'


def _apply_training(role, training):
    ids = [t['module_id'] for t in training]
    modules = {m.pk: m for m in TrainingModule.objects.filter(pk__in=ids)}
    missing = [str(i) for i in ids if i not in modules]
    if missing:
        raise ToolError(f'Unknown training module id(s): {", ".join(missing)}. See list_training_modules.')
    RoleTrainingRequirement.objects.filter(role=role).exclude(training_module_id__in=ids).delete()
    for item in training:
        RoleTrainingRequirement.objects.update_or_create(
            role=role, training_module_id=item['module_id'], defaults={'mandatory': item.get('mandatory', True)},
        )


def _apply(role, args):
    if 'name' in args:
        name = text(args['name'], 50)
        if not name:
            raise ToolError('The role needs a name.')
        clash = Role.objects.filter(name__iexact=name).exclude(pk=role.pk).first()
        if clash:
            raise ToolError(f'A role named "{clash.name}" already exists (role_id {clash.pk}).')
        set_translated(role, 'name', name)
    if 'description' in args:
        set_translated(role, 'description', text(args['description']))
    if 'icon' in args:
        role.icon = text(args['icon'], 50) or 'badge'


def _apply_relations(role, args):
    if 'training' in args:
        _apply_training(role, args['training'])
    if 'preferred_skill_ids' in args:
        skills = list(Skill.objects.filter(pk__in=args['preferred_skill_ids']))
        if len(skills) != len(set(args['preferred_skill_ids'])):
            raise ToolError('Some skill ids do not exist; see list_skills.')
        role.preferred_skills.set(skills)


@tool('create_role', 'Create a role template',
      'Add a new event role to the directory when no existing role fits, optionally requiring existing training '
      'modules. Check list_roles first to avoid duplicates.',
      ROLE_FIELDS, ['name', 'description', 'icon'])
def create_role(ctx, **args):
    role = Role(icon='badge')
    with transaction.atomic():
        _apply(role, args)
        role.save()
        _apply_relations(role, args)
    role = Role.objects.prefetch_related('roletrainingrequirement_set__training_module').get(pk=role.pk)
    return {'created': role_details(role)}


@tool('update_role', 'Edit a role template',
      'Change an event role: its name, description, icon, required training modules or preferred skills. Pass only '
      'what changes; training and preferred_skill_ids replace the whole list. Changes apply to every event using the role.',
      {'role_id': integer('The role'), **ROLE_FIELDS}, ['role_id'], idempotent=True)
def update_role(ctx, role_id, **args):
    role = get_or_error(Role.objects, role_id, 'role')
    if role.system_key or role.permanent:
        raise ToolError(f'"{role.name}" is a built-in role or permanent staff position; edit it in Town Hall.')
    if not args:
        raise ToolError('Nothing to change.')
    with transaction.atomic():
        _apply(role, args)
        role.save()
        _apply_relations(role, args)
    role = Role.objects.prefetch_related('roletrainingrequirement_set__training_module').get(pk=role.pk)
    result = {'updated': role_details(role)}
    if 'training' in args and any(t.get('mandatory', True) for t in args['training']):
        upcoming = EventRoleSlot.objects.filter(role=role, start_time__gt=timezone.now(), signups__isnull=False).count()
        if upcoming:
            result['note'] = ('People already signed up for upcoming shifts keep their places, but anyone new must '
                              'finish the required training first.')
    return result
