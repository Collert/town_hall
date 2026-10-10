"""Event task lists, kept in Bell Tower (base/belltower.py).

Each event has one planning list (preparation work for organizers) and lists for the
day itself: one per role, made automatically, plus station lists such as "Registration
desk" or "Kitchen" for whoever works there. Tasks are added and updated, never deleted.
"""
from datetime import datetime

from base import belltower
from events import belltower_sync
from events.models import EventTaskList

from . import ToolError, array, boolean, get_or_error, integer, local, obj, parse_time, string, text, time, tool
from .events import _event

TASK = obj({
    'title': string('What to do', maxLength=200),
    'description': string('Details, links or notes'),
    'due': time('When it must be done by'),
    'subtasks': array(string('A smaller step', maxLength=200), 'Smaller steps, as a checklist under the task', maxItems=20),
}, ['title'])


def _connected():
    cfg = belltower.config()
    if not belltower.is_connected(cfg):
        raise ToolError('Task lists are not set up: Bell Tower is not connected. An admin can connect it in '
                        'Town Hall under Organization > Backend.')
    return cfg


def _lists(event, cfg):
    return event.task_lists.filter(belltower_url=cfg['url']).prefetch_related('roles').select_related('area')


def planning_list(event, cfg):
    task_list = _lists(event, cfg).filter(kind=EventTaskList.PLANNING).first()
    if task_list:
        return task_list
    remote = belltower.create_list(belltower_sync.remote_name(event, 'Planning'))
    return EventTaskList.objects.create(event=event, kind=EventTaskList.PLANNING, name='Planning',
                                        belltower_url=cfg['url'], belltower_id=remote['id'])


def _task_row(task, children):
    return {
        'task_id': task['id'], 'title': task['title'], 'description': task.get('description') or '',
        'due': local(datetime.fromisoformat(task['expires_at'].replace('Z', '+00:00'))) if task.get('expires_at') else None,
        'completed': task['completed'], 'assignee': task.get('assignee_name') or task.get('assignee'),
        'subtasks': [_task_row(c, {}) for c in children.get(task['id'], [])],
    }


def _describe(task_list, tasks):
    children = {}
    for task in tasks:
        if task.get('parent'):
            children.setdefault(task['parent'], []).append(task)
    return {
        'list_id': task_list.pk,
        'name': task_list.name,
        'kind': 'planning' if task_list.kind == EventTaskList.PLANNING else 'event day',
        'roles': [r.name for r in task_list.roles.all()],
        'area': task_list.area.name if task_list.area_id else None,
        'tasks': [_task_row(t, children) for t in tasks if not t.get('parent')],
    }


@tool('get_event_tasks', 'Get an event\'s task lists',
      'All task lists of an event with their tasks: the planning list (preparation for organizers), one list per role '
      'for the event day, and station lists (e.g. "Registration desk").',
      {'event_id': integer('The event')}, ['event_id'], read_only=True)
def get_event_tasks(ctx, event_id):
    event = _event(event_id)
    cfg = _connected()
    belltower_sync.sync_event(event)  # every role at the event gets its list, as on the Tasks tab
    result = [_describe(tl, belltower.get_tasks(tl.belltower_id)) for tl in _lists(event, cfg)]
    if not any(r['kind'] == 'planning' for r in result):
        result.insert(0, {'list_id': 0, 'name': 'Planning', 'kind': 'planning', 'tasks': [],
                          'note': 'Created when the first planning task is added (add_tasks with list_id 0).'})
    return result


@tool('create_task_list', 'Create a station task list',
      'Add a task list for the event day, for a station or crew such as "Registration desk", "Kitchen" or "Setup crew". '
      'Every role at the event already has its own list. Returns the list_id for add_tasks.',
      {'event_id': integer('The event'), 'name': string('List name', maxLength=150)}, ['event_id', 'name'])
def create_task_list(ctx, event_id, name):
    event = _event(event_id)
    cfg = _connected()
    name = text(name, 150)
    if not name:
        raise ToolError('The list needs a name.')
    existing = _lists(event, cfg).filter(name__iexact=name).first()
    if existing:
        return {'list_id': existing.pk, 'name': existing.name, 'note': 'A list with this name already exists.'}
    remote = belltower.create_list(belltower_sync.remote_name(event, name))
    task_list = EventTaskList.objects.create(event=event, kind=EventTaskList.EVENT_DAY, name=name,
                                             belltower_url=cfg['url'], belltower_id=remote['id'])
    return {'list_id': task_list.pk, 'name': name}


@tool('add_tasks', 'Add tasks',
      'Add tasks to one of an event\'s lists: list_id 0 (or omitted) is the planning list for preparation work, '
      'created if needed; other ids come from get_event_tasks or create_task_list. Give preparation tasks due dates '
      'that work back from the event date.',
      {
          'event_id': integer('The event'),
          'list_id': integer('The list; 0 for the planning list', minimum=0),
          'tasks': array(TASK, 'Tasks to add', minItems=1, maxItems=40),
      }, ['event_id', 'tasks'])
def add_tasks(ctx, event_id, tasks, list_id=0):
    event = _event(event_id)
    cfg = _connected()
    task_list = get_or_error(_lists(event, cfg), list_id, 'task list at this event') if list_id else planning_list(event, cfg)
    added = []
    for task in tasks:
        title = text(task['title'], 200)
        if not title:
            continue
        due = parse_time(task['due'], f'due date of "{title}"') if task.get('due') else None
        remote = belltower.create_task(task_list.belltower_id, title, text(task.get('description')), expires_at=due)
        subtasks = [belltower.create_task(task_list.belltower_id, text(sub, 200), parent=remote['id'])['id']
                    for sub in task.get('subtasks') or [] if text(sub)]
        added.append({'task_id': remote['id'], 'title': title, 'subtask_ids': subtasks})
    return {'list_id': task_list.pk, 'list': task_list.name, 'added': added}


@tool('update_task', 'Edit a task',
      'Change a task on one of an event\'s lists, or mark it done. Pass only what changes; due "" removes the due date. '
      'Tasks cannot be deleted here.',
      {
          'event_id': integer('The event'),
          'task_id': integer('The task'),
          'title': string('New title', maxLength=200),
          'description': string('New details'),
          'due': string('New due time (YYYY-MM-DDTHH:MM), or "" for none'),
          'completed': boolean('Done or not'),
      }, ['event_id', 'task_id'], idempotent=True)
def update_task(ctx, event_id, task_id, **fields):
    event = _event(event_id)
    cfg = _connected()
    task = belltower.api('GET', f'tasks/{task_id}/')
    if not _lists(event, cfg).filter(belltower_id=task.get('list')).exists():
        raise ToolError(f'Task {task_id} is not on any of this event\'s lists.')
    changes = {}
    if 'title' in fields:
        changes['title'] = text(fields['title'], 200)
    if 'description' in fields:
        changes['description'] = text(fields['description'])
    if 'due' in fields:
        changes['expires_at'] = parse_time(fields['due'], 'due') if fields['due'] else None
    if 'completed' in fields:
        changes['completed'] = fields['completed']
    if not changes:
        raise ToolError('Nothing to change.')
    belltower.update_task(task_id, **changes)
    return {'updated': task_id, 'fields': list(fields)}
