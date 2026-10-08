from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Count, F, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from base.models import Endorsement
from education.models import TrainingModuleCompletion
from events.models import EventRoleSlot
from jobs.models import Shift

LANGUAGE_CODES = [code for code, _name in settings.LANGUAGES]

# Material Symbols offered in icon pickers. Any other icon name can be typed in.
ROLE_ICONS = [
    'person', 'medical_services', 'shield', 'restaurant', 'cleaning_services', 'support_agent',
    'sports_soccer', 'event', 'local_shipping', 'point_of_sale', 'menu_book', 'computer',
    'groups', 'child_care', 'volunteer_activism', 'construction',
]
AREA_ICONS = [
    'restaurant', 'soup_kitchen', 'theater_comedy', 'child_care', 'cleaning_services', 'local_parking',
    'storefront', 'medical_services', 'how_to_reg', 'inventory_2', 'music_note', 'park',
]
MODULE_ICONS = [
    'school', 'health_and_safety', 'local_fire_department', 'psychology', 'diversity_3',
    'handshake', 'gavel', 'menu_book', 'emergency', 'accessibility_new', 'record_voice_over', 'eco',
]
CERTIFICATE_ICONS = [
    'medical_services', 'local_police', 'school', 'verified', 'directions_run',
    'eco', 'groups', 'psychology', 'restaurant', 'construction', 'pool', 'badge',
]


def translated_fields(*names):
    """Per-language modeltranslation field names, e.g. title_en, title_es..."""
    return [f'{name}_{code}' for name in names for code in LANGUAGE_CODES]


def with_custom_icon(data):
    """Copy of POST data where a typed icon name (icon_custom) overrides the picked one."""
    data = data.copy()
    custom = data.get('icon_custom', '').strip()
    if custom:
        data['icon'] = custom
    return data


def ids_from(querydict, name):
    """Integer ids from a repeated GET/POST field, ignoring junk, order preserved."""
    ids = []
    for value in querydict.getlist(name):
        if value.isdigit() and int(value) not in ids:
            ids.append(int(value))
    return ids


def posted_ids(request, name):
    return ids_from(request.POST, name)


def paginate(request, queryset, per_page=10):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get('page'))


def page_window(page, radius=2):
    """Page numbers around the current page, with None marking gaps."""
    total = page.paginator.num_pages
    current = page.number
    pages = []
    for n in range(1, total + 1):
        if n in (1, total) or abs(n - current) <= radius:
            pages.append(n)
        elif pages and pages[-1] is not None:
            pages.append(None)
    return pages


def slots_with_counts(queryset):
    return queryset.annotate(signup_count=Count('signups', distinct=True))


def role_staffing(event, slots=None):
    """Aggregate an event's slots (or just ``slots``, annotated with signup_count) by role:
    required, filled, and slot list."""
    if slots is None:
        slots = slots_with_counts(event.role_slots.select_related('role').order_by('start_time'))
    roles = {}
    for slot in slots:
        entry = roles.setdefault(slot.role_id, {
            'role': slot.role, 'required': 0, 'filled': 0, 'slots': [],
        })
        entry['required'] += slot.required_qty
        entry['filled'] += slot.signup_count
        entry['slots'].append(slot)
    for entry in roles.values():
        required, filled = entry['required'], entry['filled']
        entry['percent'] = min(100, round(filled / required * 100)) if required else 100
        if filled >= required:
            entry['status'] = 'filled'
        elif filled * 2 < required:
            entry['status'] = 'critical'
        else:
            entry['status'] = 'understaffed'
        entry['missing'] = max(0, required - filled)
    return list(roles.values())


def unfilled_slot_count(events_queryset):
    return slots_with_counts(EventRoleSlot.objects.filter(event__in=events_queryset)).filter(
        signup_count__lt=F('required_qty')
    ).count()


def hours_between(start, end):
    """Hours worked by all shifts overlapping [start, end)."""
    now = timezone.now()
    total = 0
    for shift in Shift.objects.filter(start_time__lt=end).exclude(end_time__lt=start):
        shift_start = max(shift.start_time, start)
        shift_end = min(shift.end_time or now, end)
        if shift_end > shift_start:
            total += (shift_end - shift_start).total_seconds()
    return round(total / 3600)


def activity_feed(limit=8, event=None):
    """Recent check-ins/outs, training completions, endorsements and sign-ups."""
    items = []

    shifts = Shift.objects.select_related('user', 'role', 'event_role_slot__event').order_by('-start_time')
    if event:
        shifts = shifts.filter(event_role_slot__event=event)
    for shift in shifts[:limit]:
        where = shift.event_role_slot.event.title if shift.event_role_slot else (shift.role.name if shift.role else '')
        items.append({
            'time': shift.start_time, 'user': shift.user, 'kind': 'check_in',
            'text': _('checked in for'), 'target': where, 'accent': True,
        })
        if shift.end_time:
            items.append({
                'time': shift.end_time, 'user': shift.user, 'kind': 'check_out',
                'text': _('checked out of'), 'target': where, 'accent': False,
            })

    if not event:
        for completion in TrainingModuleCompletion.objects.select_related('user', 'training_module').order_by('-completed_at')[:limit]:
            items.append({
                'time': completion.completed_at, 'user': completion.user, 'kind': 'training',
                'text': _('completed training'), 'chip': completion.training_module.title, 'accent': False,
            })
        for endorsement in Endorsement.objects.select_related('endorser', 'endorsed').prefetch_related('skills').order_by('-timestamp')[:limit]:
            items.append({
                'time': endorsement.timestamp, 'user': endorsement.endorser, 'kind': 'endorsement',
                'text': _('endorsed %(name)s for') % {'name': endorsement.endorsed.get_full_name() or endorsement.endorsed.username},
                'chip': ', '.join(skill.name for skill in endorsement.skills.all()), 'accent': True,
            })
        for user in User.objects.filter(is_active=True).order_by('-date_joined')[:limit]:
            items.append({
                'time': user.date_joined, 'user': user, 'kind': 'joined',
                'text': _('joined the'), 'target': _('Volunteer Pool'), 'accent': False,
            })

    items.sort(key=lambda item: item['time'], reverse=True)
    return items[:limit]


def week_bounds():
    now = timezone.now()
    return now - timedelta(days=7), now
