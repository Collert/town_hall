"""Keeps an event's Bell Tower lists in step with its roles, areas, leads and sign-ups.

- Every role at an event gets a "<Role> tasks" list (``EventTaskList.roles``), made when
  the role's first slot is added or when the Tasks tab opens. With a chain of command the
  lists are per area as well (``EventTaskList.area``), so kitchen cleaners and kids zone
  cleaners don't share one. Merging lists combines their roles, so a merged list keeps
  serving every role it came from.
- Whoever signs up for a role is added to its list as a member (they can complete and
  take tasks); leaving their last slot for the list's roles takes them off again, unless
  staff made them an admin of the list.
- Leads hand out the work, so they're admins: shift leads of their slot's list, area
  leads (people signed up for the area's "Area lead" shifts) of every list in their area.
  The "Area lead" role has no list of its own.

Signals queue this work with ``belltower.run_after_commit``, so sign-ups never wait on
Bell Tower; the console's Tasks tab also calls ``sync_event`` directly.
"""
from django.contrib.auth.models import User
from django.db import transaction
from django.utils.translation import gettext as _

from base import belltower

from jobs.models import Role

from .models import EventArea, EventRoleSlot, EventTaskList


def remote_name(event, name):
    """Bell Tower's name for an event list: "<event title>: <name>"."""
    return f'{event.title}: {name}'[:200]


def _signed_up(event, roles, area_id=None):
    return User.objects.filter(commitments__event=event, commitments__role__in=roles,
                               commitments__area_id=area_id).distinct()


def _list_people(task_list):
    return _signed_up(task_list.event, task_list.roles.all(), task_list.area_id)


def _role_list(event, role, url, area_id=None):
    return (EventTaskList.objects.filter(event=event, roles=role, area_id=area_id, belltower_url=url)
            .order_by('pk').first())


def _add_person(task_list, user, admin=None):
    username = belltower.linked_username(user)
    if username:
        belltower.add_member(task_list.belltower_id, username=username, admin=admin)  # None keeps an admin an admin


def _list_leads(task_list):
    """Who should be an admin of the list: the shift leads of its slots and its area's leads."""
    leads = set(EventRoleSlot.objects.filter(
        event=task_list.event, role__in=task_list.roles.all(), area_id=task_list.area_id, lead__isnull=False,
    ).values_list('lead_id', flat=True))
    if task_list.area_id:
        leads |= set(User.objects.filter(commitments__area_id=task_list.area_id,
                                         commitments__role__system_key=Role.AREA_LEAD).values_list('pk', flat=True))
    return User.objects.filter(pk__in=leads)


def list_name(role, area=None):
    if area:
        return _('%(role)s tasks · %(area)s') % {'role': role.name, 'area': area.name}
    return _('%(role)s tasks') % {'role': role.name}


def ensure_role_list(event, role, area=None):
    """The event's list for ``role`` (in ``area``), created in Bell Tower (with everyone
    already signed up for it, leads as admins) if it doesn't exist yet. None when Bell
    Tower isn't connected, and for the built-in "Area lead" role, whose people are admins
    of their area's lists instead."""
    cfg = belltower.config()
    if not belltower.is_connected(cfg) or role.is_system:
        return None
    area_id = area.pk if area else None
    existing = _role_list(event, role, cfg['url'], area_id)
    if existing:
        return existing
    name = list_name(role, area)
    remote = belltower.create_list(remote_name(event, name))
    with transaction.atomic():
        task_list = EventTaskList.objects.create(
            event=event, kind=EventTaskList.EVENT_DAY, name=name, belltower_url=cfg['url'],
            belltower_id=remote['id'], area=area,
        )
        task_list.roles.add(role)
    first = _role_list(event, role, cfg['url'], area_id)
    if first.pk != task_list.pk:  # made at the same moment by another request: keep the older one
        task_list.delete()
        belltower.delete_list(remote['id'])
        return first
    for user in _list_people(task_list):
        _add_person(task_list, user)
    for user in _list_leads(task_list):
        _add_person(task_list, user, admin=True)
    return task_list


def sync_event(event):
    """Make sure every role (per area) at the event has its list."""
    slots = EventRoleSlot.objects.filter(event=event, role__system_key__isnull=True).select_related('role', 'area')
    pairs = {(slot.role, slot.area) for slot in slots}
    for role, area in sorted(pairs, key=lambda p: (p[1].name if p[1] else '', p[0].name)):
        ensure_role_list(event, role, area)


def role_slot_added(event_id, role_id, area_id=None):
    slot = (EventRoleSlot.objects.filter(event_id=event_id, role_id=role_id, area_id=area_id)
            .select_related('event', 'role', 'area').first())
    if slot:
        ensure_role_list(slot.event, slot.role, slot.area)


def _drop_if_gone(task_list, users):
    """Take people off a list once they're no longer signed up for any slot it serves
    (admins staff added stay)."""
    for user in users:
        if not _list_people(task_list).filter(pk=user.pk).exists():
            username = belltower.linked_username(user, create=False)
            if username:
                belltower.remove_member_if_plain(task_list.belltower_id, username)


def signups_changed(slot_ids, user_ids, added):
    """People joined (``added``) or left these slots: update the role lists."""
    for slot in EventRoleSlot.objects.filter(pk__in=slot_ids).select_related('event', 'role', 'area'):
        if slot.is_area_lead:
            if slot.area_id:
                area_leads_changed(slot.area_id, user_ids if added else (), () if added else user_ids)
            continue
        if added:
            task_list = ensure_role_list(slot.event, slot.role, slot.area)
        else:
            task_list = _role_list(slot.event, slot.role, belltower.config()['url'], slot.area_id)
        if task_list is None:
            continue
        users = User.objects.filter(pk__in=user_ids)
        if added:
            for user in users:
                _add_person(task_list, user)
        else:
            _drop_if_gone(task_list, users)


def slot_moved(slot_id, old_area_id):
    """A slot changed areas: its people (and lead) move to the new area's list."""
    slot = EventRoleSlot.objects.filter(pk=slot_id).select_related('event', 'role', 'area').first()
    if slot is None or old_area_id == slot.area_id:
        return
    task_list = ensure_role_list(slot.event, slot.role, slot.area)
    if task_list is None:
        return
    people = list(slot.signups.all())
    for user in people:
        _add_person(task_list, user, admin=True if user.pk == slot.lead_id else None)
    old = _role_list(slot.event, slot.role, belltower.config()['url'], old_area_id)
    if old:
        _demote(old, [u for u in people if u.pk == slot.lead_id])
        _drop_if_gone(old, people)


def _demote(task_list, users):
    """Turn former leads back into plain members (unless they still lead something there)."""
    cfg = belltower.config()
    still_leading = set(_list_leads(task_list).values_list('pk', flat=True))
    for user in users:
        if user.pk in still_leading:
            continue
        username = belltower.linked_username(user, create=False)
        if username and username != cfg['username']:
            try:
                belltower.add_member(task_list.belltower_id, username=username, admin=False)
            except belltower.BellTowerError as exc:
                if exc.status != 404:
                    raise


def slot_lead_changed(slot_id, old_lead_id):
    """A slot's shift lead changed: the new one becomes an admin of its list, the old one
    goes back to being a member (or leaves, if they left the slot)."""
    slot = EventRoleSlot.objects.filter(pk=slot_id).select_related('event', 'role', 'area', 'lead').first()
    if slot is None:
        return
    task_list = ensure_role_list(slot.event, slot.role, slot.area)
    if task_list is None:
        return
    if slot.lead:
        _add_person(task_list, slot.lead, admin=True)
    old = User.objects.filter(pk=old_lead_id).first()
    if old:
        _demote(task_list, [old])
        _drop_if_gone(task_list, [old])


def area_leads_changed(area_id, added_ids=(), removed_ids=()):
    """Area leads are admins of every list in their area."""
    area = EventArea.objects.filter(pk=area_id).select_related('event').first()
    if area is None:
        return
    cfg = belltower.config()
    if not belltower.is_connected(cfg):
        return
    roles = {slot.role for slot in area.work_slots().select_related('role')}
    lists = [ensure_role_list(area.event, role, area) for role in sorted(roles, key=lambda r: r.name)]
    for task_list in lists:
        for user in User.objects.filter(pk__in=added_ids):
            _add_person(task_list, user, admin=True)
        removed = list(User.objects.filter(pk__in=removed_ids))
        _demote(task_list, removed)
        for user in removed:
            username = belltower.linked_username(user, create=False)
            if username and not _list_people(task_list).filter(pk=user.pk).exists() \
                    and not _list_leads(task_list).filter(pk=user.pk).exists():
                belltower.remove_member_if_plain(task_list.belltower_id, username)


def role_lists_for(event):
    """{(role_id, area_id): EventTaskList} for the event's role lists on the connected server."""
    cfg = belltower.config()
    if not belltower.is_connected(cfg):
        return {}
    lists = EventTaskList.objects.filter(event=event, belltower_url=cfg['url']).prefetch_related('roles').order_by('-pk')
    return {(role.pk, tl.area_id): tl for tl in lists for role in tl.roles.all()}  # oldest list wins a role
