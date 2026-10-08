"""Event > Tasks: the event's Bell Tower lists (base/belltower.py).

Each event has one planning list (prep for organizers) and any number of lists for
the day itself. Tasks live only in Bell Tower; every action here calls its API and
re-renders the list's card from the fresh data.

Lists are shared by email: Bell Tower finds the account with that address. Members
can complete tasks and take them on; admins (Town Hall's own account always is one)
also add, edit and assign tasks.
"""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from base import belltower
from events import belltower_sync
from events.models import Event, EventTaskList

from ..decorators import staff_required


def _is_htmx(request):
    return request.headers.get('HX-Request') == 'true'


def _lists(event, cfg):
    return event.task_lists.filter(belltower_url=cfg['url'])


def _get_list(event_id, list_id):
    event = get_object_or_404(Event, pk=event_id)
    cfg = belltower.config()
    return event, get_object_or_404(_lists(event, cfg), pk=list_id), cfg


def _parse_local(value):
    """A datetime-local input ("2026-10-07T14:30") in the current timezone, or None."""
    value = parse_datetime(value or '')
    if value is not None and timezone.is_naive(value):
        value = timezone.make_aware(value)
    return value


def _prepare(task):
    task['expires'] = parse_datetime(task['expires_at']) if task.get('expires_at') else None
    return task


def _tree(tasks):
    """Top-level tasks with their subtasks attached, open ones first."""
    by_parent = {}
    for task in tasks:
        by_parent.setdefault(task['parent'], []).append(_prepare(task))
    for task in tasks:
        task['children'] = by_parent.get(task['id'], [])
    top = by_parent.get(None, [])
    return [t for t in top if not t['completed']], [t for t in top if t['completed']]


def _member_label(member, cfg):
    name = member['name'] or member['email'] or member['username']
    return _('%(name)s (Town Hall)') % {'name': name} if member['username'] == cfg['username'] else name


_remote_name = belltower_sync.remote_name


def _sync_name(event, task_list, remote_name):
    """Pick up a rename (or merge) done in Bell Tower itself."""
    prefix = f'{event.title}: '
    name = (remote_name[len(prefix):] if remote_name.startswith(prefix) else remote_name)[:200]
    if name and name != task_list.name:
        task_list.name = name
        task_list.save(update_fields=['name'])


def _render_card(request, event, task_list, cfg, error=None, editing=None, renaming=False, oob=False):
    """The list's card, built from Bell Tower's current data. ``editing`` is the id of a
    task to show as an edit form, ``renaming`` shows the name as a form, and ``oob``
    marks the card for an out-of-band swap (a second card in one response). A list
    that's gone from Bell Tower is forgotten here too."""
    context = {
        'event': event, 'task_list': task_list, 'error': error, 'belltower': cfg,
        'editing': editing, 'renaming': renaming, 'oob': oob,
    }
    try:
        remote = belltower.get_list(task_list.belltower_id)
        tasks = belltower.get_tasks(task_list.belltower_id)
    except belltower.BellTowerError as exc:
        if exc.status == 404:
            task_list.delete()
            messages.warning(request, _('"%(name)s" was deleted in Bell Tower, so it was removed here.') % {'name': task_list.name})
            return HttpResponse('')
        context['error'] = str(exc)
        return render(request, 'console/partials/task_list_card.html', context)
    _sync_name(event, task_list, remote.get('name', ''))
    open_tasks, done_tasks = _tree(tasks)
    total = len(tasks)
    done = sum(t['completed'] for t in tasks)
    members = remote.get('members', [])
    context.update({
        'open_tasks': open_tasks,
        'done_tasks': done_tasks,
        'total': total,
        'done': done,
        'progress': round(done / total * 100) if total else 0,
        # Town Hall's own account must stay an admin member, or Town Hall loses the list.
        'members': [m for m in members if m['username'] != cfg['username']],
        'assignees': [(m['username'], _member_label(m, cfg)) for m in members],
        'web_url': belltower.list_web_url(task_list.belltower_id, cfg),
    })
    return render(request, 'console/partials/task_list_card.html', context)


def _share_suggestions(event):
    """Town Hall people a list is likely shared with: staff, coordinators and the
    event's volunteers. Bell Tower matches them by email."""
    return (
        User.objects.filter(is_active=True)
        .filter(Q(is_staff=True) | Q(coordinated_events=event) | Q(commitments__event=event))
        .exclude(email='').distinct().order_by('first_name', 'username')
    )


@staff_required
def event_tasks(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    cfg = belltower.config()
    connected = belltower.is_connected(cfg)
    if connected:
        try:
            belltower_sync.sync_event(event)  # a "<Role> tasks" list for every role
        except belltower.BellTowerError as exc:
            messages.warning(request, _("Couldn't set up the role lists in Bell Tower: %(error)s") % {'error': exc})
    lists = list(_lists(event, cfg).prefetch_related('roles')) if connected else []

    # Planning hides itself once the event starts; staff can show or hide it either way.
    started = event.start_date <= timezone.now()
    session_key = f'tasks_planning_hidden_{event.pk}'
    if request.GET.get('planning') in ('show', 'hide'):
        request.session[session_key] = request.GET['planning'] == 'hide'
        return redirect('console_event_tasks', event_id=event.pk)
    planning_hidden = request.session.get(session_key, started)

    return render(request, 'console/event_tasks.html', {
        'event': event,
        'connected': connected,
        'belltower': cfg,
        'started': started,
        'planning_hidden': planning_hidden,
        'planning': next((tl for tl in lists if tl.kind == EventTaskList.PLANNING), None),
        'event_lists': [tl for tl in lists if tl.kind == EventTaskList.EVENT_DAY],
        'suggestions': _share_suggestions(event) if connected else [],
    })


@staff_required
@require_POST
def list_create(request, event_id):
    """Create a Bell Tower list for the event. The planning list is made the first time
    the Tasks tab shows it; day-of lists are named by staff."""
    event = get_object_or_404(Event, pk=event_id)
    cfg = belltower.config()
    kind = request.POST.get('kind')
    if kind == EventTaskList.PLANNING:
        existing = _lists(event, cfg).filter(kind=EventTaskList.PLANNING).first()
        if existing:
            return _render_card(request, event, existing, cfg)
        name = _('Planning')
    else:
        kind = EventTaskList.EVENT_DAY
        name = request.POST.get('name', '').strip()[:150]
        if not name:
            messages.error(request, _('Give the list a name, e.g. "Setup crew".'))
            return HttpResponse(status=204) if _is_htmx(request) else redirect('console_event_tasks', event_id=event.pk)
    try:
        # Bell Tower lists are shared by the whole organization; the event title keeps them apart.
        remote = belltower.create_list(_remote_name(event, name))
    except belltower.BellTowerError as exc:
        messages.error(request, _("Couldn't create the list in Bell Tower: %(error)s") % {'error': exc})
        return HttpResponse(status=204) if _is_htmx(request) else redirect('console_event_tasks', event_id=event.pk)
    task_list = EventTaskList.objects.create(
        event=event, kind=kind, name=name, belltower_url=cfg['url'], belltower_id=remote['id'],
    )
    if not _is_htmx(request):
        return redirect('console_event_tasks', event_id=event.pk)
    return _render_card(request, event, task_list, cfg)


@staff_required
def list_card(request, event_id, list_id):
    event, task_list, cfg = _get_list(event_id, list_id)
    return _render_card(request, event, task_list, cfg, renaming=bool(request.GET.get('rename')))


@staff_required
@require_POST
def list_rename(request, event_id, list_id):
    event, task_list, cfg = _get_list(event_id, list_id)
    name = request.POST.get('name', '').strip()[:150]
    if name and name != task_list.name:
        try:
            belltower.update_list(task_list.belltower_id, name=_remote_name(event, name))
        except belltower.BellTowerError as exc:
            return _render_card(request, event, task_list, cfg, error=str(exc), renaming=True)
        task_list.name = name
        task_list.save(update_fields=['name'])
    return _render_card(request, event, task_list, cfg)


@staff_required
@require_POST
def list_merge(request, event_id, list_id):
    """Merge day-of list ``source`` into this one (a list card dropped on another).
    Tasks, members and admins move over; the name becomes "This & Source"."""
    event, target, cfg = _get_list(event_id, list_id)
    source = get_object_or_404(_lists(event, cfg).exclude(pk=target.pk), pk=request.POST.get('source') or 0)
    if EventTaskList.PLANNING in (target.kind, source.kind):
        return _render_card(request, event, target, cfg, error=_('The planning list can\'t be merged.'))
    target_roles, source_roles = target.roles.exists(), source.roles.exists()
    if target_roles and source_roles and target.area_id != source.area_id:
        return _render_card(request, event, target, cfg, error=_('Lists from different areas can\'t be merged.'))
    name = f'{target.name} & {source.name}'[:150]
    try:
        belltower.merge_lists(target.belltower_id, source.belltower_id, name=_remote_name(event, name))
    except belltower.BellTowerError as exc:
        return _render_card(request, event, target, cfg, error=str(exc))
    target.name = name
    if source_roles and not target_roles:
        target.area_id = source.area_id
    target.save(update_fields=['name', 'area'])
    # The merged list serves the source's roles too, so their sign-ups land here from now on.
    target.roles.add(*source.roles.all())
    source_pk = source.pk
    source.delete()
    response = _render_card(request, event, target, cfg)
    response.write(f'<div id="task-list-{source_pk}" hx-swap-oob="delete"></div>')
    return response


@staff_required
@require_POST
def task_move(request, event_id, list_id):
    """Move ``task`` from list ``source`` into this one (a task dropped on another card).
    Answers with both cards: this one in place, the source one out of band."""
    event, target, cfg = _get_list(event_id, list_id)
    source = get_object_or_404(_lists(event, cfg), pk=request.POST.get('source') or 0)
    try:
        task_id = int(request.POST.get('task', ''))
        _check_task(source, task_id)
        belltower.move_task(task_id, target.belltower_id)
    except (belltower.BellTowerError, ValueError) as exc:
        return _render_card(request, event, target, cfg, error=str(exc))
    response = _render_card(request, event, target, cfg)
    if source.pk != target.pk:
        response.write(_render_card(request, event, source, cfg, oob=True).content)
    return response


@staff_required
@require_POST
def list_delete(request, event_id, list_id):
    event, task_list, cfg = _get_list(event_id, list_id)
    if task_list.roles.exists():
        return _render_card(request, event, task_list, cfg, error=_("A role's list stays while the role is at the event."))
    try:
        belltower.delete_list(task_list.belltower_id)
    except belltower.BellTowerError as exc:
        if exc.status != 404:
            messages.error(request, _("Couldn't delete the list in Bell Tower: %(error)s") % {'error': exc})
            return _render_card(request, event, task_list, cfg)
    task_list.delete()
    messages.success(request, _('"%(name)s" deleted.') % {'name': task_list.name})
    if not _is_htmx(request):
        return redirect('console_event_tasks', event_id=event.pk)
    return HttpResponse('')


@staff_required
@require_POST
def list_share(request, event_id, list_id):
    """Share with ``email`` (``admin`` lets them add and assign tasks; an existing member's
    email changes their role), set a member's role with ``role`` (a username), or stop
    sharing with ``remove`` (a username). Town Hall's own account is never touched."""
    event, task_list, cfg = _get_list(event_id, list_id)
    email = request.POST.get('email', '').strip()
    role = request.POST.get('role', '').strip()
    remove = request.POST.get('remove', '').strip()
    admin = bool(request.POST.get('admin'))
    try:
        if email:
            belltower.add_member(task_list.belltower_id, email, admin=admin)
        elif role and role != cfg['username']:
            belltower.add_member(task_list.belltower_id, username=role, admin=admin)
        elif remove and remove != cfg['username']:
            belltower.remove_member(task_list.belltower_id, remove)
    except belltower.BellTowerError as exc:
        if exc.status == 404 and email:
            error = _('No Bell Tower account uses %(email)s. They need to sign in to Bell Tower once with that email.') % {'email': email}
        else:
            error = str(exc)
        return _render_card(request, event, task_list, cfg, error=error)
    return _render_card(request, event, task_list, cfg)


def _check_task(task_list, task_id):
    """Only touch tasks that belong to this list (the key can reach other lists too)."""
    task = belltower.api('GET', f'tasks/{task_id}/')
    if task.get('list') != task_list.belltower_id:
        raise belltower.BellTowerError(_('That task is not in this list.'), 404)
    return task


def _expiry(request):
    """``expires_in`` is a quick choice in minutes from now, "custom" for the date picker
    (``expires_at``), or empty for no expiry."""
    choice = request.POST.get('expires_in', '')
    if choice.isdigit():
        return timezone.now() + timedelta(minutes=int(choice))
    if choice == 'custom' or (not choice and request.POST.get('expires_at')):
        return _parse_local(request.POST.get('expires_at'))
    return None


def _task_fields(request):
    """Title, description, expiry and assignee from an add or edit form."""
    return {
        'title': request.POST.get('title', '').strip()[:200],
        'description': request.POST.get('description', '').strip(),
        'expires_at': _expiry(request),
        'assignee': request.POST.get('assignee') or None,
    }


@staff_required
@require_POST
def task_add(request, event_id, list_id):
    event, task_list, cfg = _get_list(event_id, list_id)
    fields = _task_fields(request)
    if not fields['title']:
        return _render_card(request, event, task_list, cfg)
    parent = request.POST.get('parent') or None
    try:
        if parent:
            parent = _check_task(task_list, int(parent))['id']
        belltower.create_task(task_list.belltower_id, parent=parent, **fields)
    except (belltower.BellTowerError, ValueError) as exc:
        return _render_card(request, event, task_list, cfg, error=str(exc))
    return _render_card(request, event, task_list, cfg)


@staff_required
def task_edit(request, event_id, list_id, task_id):
    """GET shows the task as a form inside its card; POST saves it."""
    event, task_list, cfg = _get_list(event_id, list_id)
    if request.method != 'POST':
        return _render_card(request, event, task_list, cfg, editing=task_id)
    fields = _task_fields(request)
    if not fields['title']:
        return _render_card(request, event, task_list, cfg, error=_('A task needs a title.'), editing=task_id)
    try:
        _check_task(task_list, task_id)
        belltower.update_task(task_id, **fields)
    except belltower.BellTowerError as exc:
        return _render_card(request, event, task_list, cfg, error=str(exc), editing=task_id)
    return _render_card(request, event, task_list, cfg)


@staff_required
@require_POST
def task_toggle(request, event_id, list_id, task_id):
    event, task_list, cfg = _get_list(event_id, list_id)
    try:
        task = _check_task(task_list, task_id)
        belltower.update_task(task_id, completed=not task['completed'])
    except belltower.BellTowerError as exc:
        return _render_card(request, event, task_list, cfg, error=str(exc))
    return _render_card(request, event, task_list, cfg)


@staff_required
@require_POST
def task_delete(request, event_id, list_id, task_id):
    event, task_list, cfg = _get_list(event_id, list_id)
    try:
        _check_task(task_list, task_id)
        belltower.delete_task(task_id)
    except belltower.BellTowerError as exc:
        return _render_card(request, event, task_list, cfg, error=str(exc))
    return _render_card(request, event, task_list, cfg)
