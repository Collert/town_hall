"""Impact points: how each kind of contribution is scored, and the ledger entries it creates.

Every award is a `PointsEntry`; a volunteer's `Profile.impact_points` is their sum. The rules
live in the `PointsRules` singleton (Console > Organization > Points).

Shift = points per hour x hours x role weight x (1 + reach bonus) x (1 + clutch bonuses)
  * role weight: the role's own `points_weight`, else 1.0 up to `max_role_weight` by how long
    its training is compared with other roles (Role.complexity_level, 0-4)
  * reach: people served per volunteer (event attendees shared among everyone who signed up,
    or a permanent role's beneficiaries per hour x hours). +10% per doubling, capped.
  * clutch: walk-in cover at the kiosk, last-minute sign-up to an understaffed slot, and
    shifts that start early or end late. These add up.
"""
import math
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import PointsEntry, PointsRules

REACH_PERCENT_PER_DOUBLING = 10


def role_weight(role, rules):
    if role is None:
        return 1.0
    if role.points_weight:
        return float(role.points_weight)
    return 1 + role.complexity_level / 4 * (float(rules.max_role_weight) - 1)


def _reach(shift, hours):
    """People served per volunteer during this shift, or None when nobody tracks it."""
    slot = shift.event_role_slot
    if slot:
        attendees = slot.event.attendees or 0
        if attendees <= 0:
            return None
        return attendees / max(1, slot.event.volunteers().count())
    role = shift.role
    if role and role.permanent and role.regular_number_of_beneficiaries:
        return role.regular_number_of_beneficiaries * hours
    return None


def is_off_hours(start, end, rules):
    """Starts before `early_start_hour` or ends after `late_end_hour` (or past midnight), local time."""
    start, end = timezone.localtime(start), timezone.localtime(end)
    ends_late = end.date() > start.date() or (end.hour, end.minute) > (rules.late_end_hour, 0)
    return start.hour < rules.early_start_hour or ends_late


def shift_points(shift, rules=None):
    """(points, details) for a finished shift under the current rules."""
    rules = rules or PointsRules.get()
    if not shift.end_time or not shift.start_time:
        return 0, {}
    hours = shift.duration()
    role = shift.event_role_slot.role if shift.event_role_slot else shift.role
    weight = role_weight(role, rules)

    served = _reach(shift, hours)
    reach_percent = 0
    if served:
        reach_percent = min(rules.reach_cap_percent, round(REACH_PERCENT_PER_DOUBLING * math.log2(1 + served)))

    bonuses = {}
    if shift.clutched:
        bonuses['walk_in'] = rules.walk_in_bonus_percent
    slot = shift.event_role_slot
    if slot and not shift.clutched and slot.signup_log.filter(user_id=shift.user_id, last_minute=True).exists():
        bonuses['last_minute'] = rules.last_minute_bonus_percent
    # Scheduled times for event slots (checking in a few minutes early shouldn't count), actual otherwise
    start, end = (slot.start_time, slot.end_time) if slot else (shift.start_time, shift.end_time)
    if is_off_hours(start, end, rules):
        bonuses['off_hours'] = rules.off_hours_bonus_percent
    clutch_percent = sum(bonuses.values())

    points = rules.points_per_hour * hours * weight * (1 + reach_percent / 100) * (1 + clutch_percent / 100)
    # Half up, so 52.5 is 53 (Python's round() would give 52)
    return math.floor(points + 0.5), {
        'hours': round(hours, 2),
        'per_hour': rules.points_per_hour,
        'weight': round(weight, 2),
        'served': round(served, 1) if served else None,
        'reach_percent': reach_percent,
        'bonuses': bonuses,
    }


def award_shift(shift):
    """Create or refresh the ledger entry for a shift; remove it if the shift is reopened."""
    from jobs.models import Shift

    if not shift.end_time:
        PointsEntry.objects.filter(shift=shift).delete()
        Shift.objects.filter(pk=shift.pk).update(end_impact_points=None)
        return None
    amount, details = shift_points(shift)
    with transaction.atomic():
        entry, _created = PointsEntry.objects.update_or_create(
            shift=shift,
            defaults={
                'user_id': shift.user_id, 'amount': amount, 'source': PointsEntry.SHIFT, 'details': details,
                'event_id': shift.event_role_slot.event_id if shift.event_role_slot_id else None,
                'created_at': shift.end_time,
            },
        )
        Shift.objects.filter(pk=shift.pk).update(end_impact_points=amount)
    shift.end_impact_points = amount
    return entry


def training_points(module, rules=None):
    rules = rules or PointsRules.get()
    minutes = module.get_total_length()
    amount = round(minutes * float(rules.training_points_per_minute))
    return max(rules.training_points_min, min(rules.training_points_cap, amount)), minutes


def award_training(completion):
    """One-time award for finishing a module."""
    module = completion.training_module
    if PointsEntry.objects.filter(user_id=completion.user_id, source=PointsEntry.TRAINING, training_module=module).exists():
        return None
    amount, minutes = training_points(module)
    return PointsEntry.objects.create(
        user_id=completion.user_id, amount=amount, source=PointsEntry.TRAINING, training_module=module,
        details={'minutes': minutes}, created_at=completion.completed_at,
    )


def award_endorsement(endorsement):
    """Small bonus for being endorsed by a teammate from the same event. One per event, a monthly
    cap, and nothing for endorsing back someone who just endorsed you."""
    rules = PointsRules.get()
    event, endorsed, endorser = endorsement.event, endorsement.endorsed, endorsement.endorser
    if not event or not rules.endorsement_points or endorsed == endorser:
        return None
    team = event.volunteers()
    if not team.filter(pk=endorsed.pk).exists() or not team.filter(pk=endorser.pk).exists():
        return None
    earned = PointsEntry.objects.filter(user=endorsed, source=PointsEntry.ENDORSEMENT)
    if earned.filter(event=event).exists():
        return None
    if earned.filter(created_at__gte=timezone.now() - timedelta(days=30)).count() >= rules.endorsement_monthly_cap:
        return None
    from .models import Endorsement
    if Endorsement.objects.filter(endorser=endorsed, endorsed=endorser, event=event, timestamp__lt=endorsement.timestamp).exists():
        return None
    return PointsEntry.objects.create(
        user=endorsed, amount=rules.endorsement_points, source=PointsEntry.ENDORSEMENT,
        endorsement=endorsement, event=event, details={'from': endorser.get_full_name() or endorser.username},
    )


def adjust(user, amount, reason, by):
    return PointsEntry.objects.create(user=user, amount=amount, source=PointsEntry.ADJUSTMENT, reason=reason, created_by=by)


# ---------------------------------------------------------------- display

BONUS_LABELS = {
    'walk_in': lambda: _('Walk-in cover'),
    'last_minute': lambda: _('Last-minute sign-up'),
    'off_hours': lambda: _('Early or late shift'),
}


def entry_label(entry):
    if entry.source == PointsEntry.SHIFT and entry.shift_id:
        shift = entry.shift
        role = shift.role.name if shift.role else _('Shift')
        if shift.event_role_slot_id:
            return _('%(role)s at %(event)s') % {'role': role, 'event': shift.event_role_slot.event.title}
        return role
    if entry.source == PointsEntry.TRAINING:
        return _('Completed %(module)s') % {'module': entry.training_module.title if entry.training_module else _('a training module')}
    if entry.source == PointsEntry.ENDORSEMENT:
        return _('Endorsed by %(name)s') % {'name': entry.details.get('from', _('a teammate'))}
    return entry.reason or _('Adjustment')


def entry_factors(entry):
    """Short chips explaining a shift's amount, e.g. "2.5 h", "x1.5 role", "+20% reach"."""
    d = entry.details or {}
    if entry.source != PointsEntry.SHIFT or not d:
        return []
    chips = [_('%(h)s h') % {'h': d.get('hours')}]
    if d.get('weight') and d['weight'] != 1:
        chips.append(_('×%(w)s role') % {'w': d['weight']})
    if d.get('reach_percent'):
        chips.append(_('+%(p)s%% reach') % {'p': d['reach_percent']})
    for key, percent in (d.get('bonuses') or {}).items():
        chips.append('%s +%s%%' % (BONUS_LABELS.get(key, lambda: key)(), percent))
    return chips


def rebuild(users=None):
    """Recalculate every finished shift and backfill training awards under the current rules."""
    from django.contrib.auth.models import User

    from education.models import TrainingModuleCompletion
    from jobs.models import Shift

    shifts = Shift.objects.filter(end_time__isnull=False).select_related('event_role_slot__event', 'event_role_slot__role', 'role')
    completions = TrainingModuleCompletion.objects.select_related('training_module')
    if users is not None:
        shifts, completions = shifts.filter(user__in=users), completions.filter(user__in=users)
    for shift in shifts:
        award_shift(shift)
    for completion in completions:
        award_training(completion)
    for user in (users if users is not None else User.objects.all()):
        user.profile.recalculate_impact_points()
