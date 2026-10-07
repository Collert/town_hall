"""Keeps an event's Bell Tower lists in step with its roles and sign-ups.

- Every role at an event gets a "<Role> tasks" list (``EventTaskList.roles``), made when
  the role's first slot is added or when the Tasks tab opens. Merging lists combines
  their roles, so a merged list keeps serving every role it came from.
- Whoever signs up for a role is added to its list as a member (they can complete and
  take tasks); leaving their last slot for the list's roles takes them off again, unless
  staff made them an admin of the list.

Signals queue this work with ``belltower.run_after_commit``, so sign-ups never wait on
Bell Tower; the console's Tasks tab also calls ``sync_event`` directly.
"""
from django.contrib.auth.models import User
from django.db import transaction
from django.utils.translation import gettext as _

from base import belltower

from .models import EventRoleSlot, EventTaskList


def remote_name(event, name):
    """Bell Tower's name for an event list: "<event title>: <name>"."""
    return f'{event.title}: {name}'[:200]


def _signed_up(event, roles):
    return User.objects.filter(commitments__event=event, commitments__role__in=roles).distinct()


def _role_list(event, role, url):
    return EventTaskList.objects.filter(event=event, roles=role, belltower_url=url).order_by('pk').first()


def _add_person(task_list, user):
    username = belltower.linked_username(user)
    if username:
        belltower.add_member(task_list.belltower_id, username=username)  # keeps an admin an admin


def ensure_role_list(event, role):
    """The event's list for ``role``, created in Bell Tower (with everyone already signed
    up for the role) if it doesn't exist yet. None when Bell Tower isn't connected."""
    cfg = belltower.config()
    if not belltower.is_connected(cfg):
        return None
    existing = _role_list(event, role, cfg['url'])
    if existing:
        return existing
    name = _('%(role)s tasks') % {'role': role.name}
    remote = belltower.create_list(remote_name(event, name))
    with transaction.atomic():
        task_list = EventTaskList.objects.create(
            event=event, kind=EventTaskList.EVENT_DAY, name=name, belltower_url=cfg['url'], belltower_id=remote['id'],
        )
        task_list.roles.add(role)
    first = _role_list(event, role, cfg['url'])
    if first.pk != task_list.pk:  # made at the same moment by another request: keep the older one
        task_list.delete()
        belltower.delete_list(remote['id'])
        return first
    for user in _signed_up(event, [role]):
        _add_person(task_list, user)
    return task_list


def sync_event(event):
    """Make sure every role at the event has its list."""
    roles = {slot.role for slot in EventRoleSlot.objects.filter(event=event).select_related('role')}
    for role in sorted(roles, key=lambda r: r.name):
        ensure_role_list(event, role)


def role_slot_added(event_id, role_id):
    slot = EventRoleSlot.objects.filter(event_id=event_id, role_id=role_id).select_related('event', 'role').first()
    if slot:
        ensure_role_list(slot.event, slot.role)


def signups_changed(slot_ids, user_ids, added):
    """People joined (``added``) or left these slots: update the role lists."""
    for slot in EventRoleSlot.objects.filter(pk__in=slot_ids).select_related('event', 'role'):
        if added:
            task_list = ensure_role_list(slot.event, slot.role)
        else:
            task_list = _role_list(slot.event, slot.role, belltower.config()['url'])
        if task_list is None:
            continue
        for user in User.objects.filter(pk__in=user_ids):
            if added:
                _add_person(task_list, user)
            elif not _signed_up(slot.event, task_list.roles.all()).filter(pk=user.pk).exists():
                username = belltower.linked_username(user, create=False)
                if username:
                    belltower.remove_member_if_plain(task_list.belltower_id, username)


def role_lists_for(event):
    """{role_id: EventTaskList} for the event's role lists on the connected server."""
    cfg = belltower.config()
    if not belltower.is_connected(cfg):
        return {}
    lists = EventTaskList.objects.filter(event=event, belltower_url=cfg['url']).prefetch_related('roles').order_by('-pk')
    return {role.pk: tl for tl in lists for role in tl.roles.all()}  # oldest list wins a role
