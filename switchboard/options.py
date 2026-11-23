"""Lists Switchboard offers as dropdowns: `GET /switchboard/v1/options/<name>` (the protocol's
"Options from the building"). A manifest param names one with `"options": "<name>"`.

Each option's `value` is exactly what the param accepts: where the param takes a name, the name
`actions._named` matches (in the organization's default language); where several things share
that name, or it can't be matched as typed, the ID instead. Where the param takes an ID, the ID,
with a label a person can read. Only what the actions already accept and reveal is listed, in a
fixed number of queries per list.
"""
from collections import Counter

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.db.models.functions import Lower
from django.utils import timezone

OPTIONS_LIMIT = 500


def _column(model, field):
    """The default language's column of a translated field, else the field itself."""
    from console.utils import lang_field
    column = lang_field(field, settings.LANGUAGE_CODE)
    try:
        model._meta.get_field(column)
    except FieldDoesNotExist:
        return field
    return column


def _by_name(matched, listed=None, field='name', label=None):
    """Options for a param that `_named(matched, ...)` resolves. `listed` narrows what is
    offered (default: everything `matched` holds); `label(item, name)` dresses the label up."""
    column = _column(matched.model, field)
    seen = Counter(name.lower() for name in matched.order_by().values_list(column, flat=True) if name)
    shared = {name for name, times in seen.items() if times > 1}
    rows = (matched if listed is None else listed).order_by(Lower(column), 'pk')[:OPTIONS_LIMIT]
    options = []
    for item in rows:
        exact, shown = getattr(item, column) or '', getattr(item, field) or ''
        # A name `_named` would match as it is: not empty, not taken for an ID, spaced as typed, not shared.
        twin = exact.lower() in shared
        usable = exact and not exact.isdigit() and exact == ' '.join(exact.split()) and not twin
        text = shown or exact or f'#{item.pk}'
        options.append({
            'value': exact if usable else str(item.pk),
            # Things that share a name are told apart by their ID.
            'label': (label(item, text) if label else text) + (f' (ID {item.pk})' if twin else ''),
        })
    return options


def roles():
    """Every role a setting can name: never the built-in Area lead."""
    from .actions import _roles
    return _by_name(_roles(), label=lambda role, name: f'{name} (permanent)' if role.permanent else name)


def permanent_roles():
    from .actions import _roles
    return _by_name(_roles(), _roles().filter(permanent=True))


def event_roles():
    from .actions import _roles
    return _by_name(_roles(), _roles().filter(permanent=False))


def training_modules():
    """Modules volunteers take: never the staff-only guides."""
    from education.models import TrainingModule
    modules = TrainingModule.objects.filter(staff_only=False)
    return _by_name(modules, field='title',
                    label=lambda module, title: title if module.published else f'{title} (not published)')


def certificates():
    from education.models import ExternalCertificate
    return _by_name(ExternalCertificate.objects.all(),
                    label=lambda certificate, name: f'{name} ({certificate.issuer})' if certificate.issuer else name)


def skills():
    from education.models import Skill
    return _by_name(Skill.objects.all())


def venues():
    from base.models import Venue
    return _by_name(Venue.objects.all())


def _day(moment):
    moment = timezone.localtime(moment)
    return f'{moment:%a} {moment.day} {moment:%b %Y}'


def events():
    """Events by ID: the ones not over yet, soonest first, then past ones, latest first."""
    from .actions import _events
    now = timezone.now()
    found = list(_events().filter(end_date__gte=now).order_by('start_date', 'pk')[:OPTIONS_LIMIT])
    if len(found) < OPTIONS_LIMIT:
        found += _events().filter(end_date__lt=now).order_by('-start_date', '-pk')[:OPTIONS_LIMIT - len(found)]
    return [{'value': str(event.pk),
             'label': f'{event.title} · {_day(event.start_date)}' + ('' if event.published else ' (hidden)')}
            for event in found]


SOURCES = {
    'roles': roles,
    'roles.permanent': permanent_roles,
    'roles.event': event_roles,
    'training.modules': training_modules,
    'certificates': certificates,
    'skills': skills,
    'venues': venues,
    'events': events,
}
