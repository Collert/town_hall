"""Starting, keeping alive and ending permanent-role shifts through the API.

Another application (through an API key) starts a shift for a person, identified by their
6-digit Town Hall ID (`Profile.id_code`, the kiosk ID), in a permanent role they're assigned to.
While the person is working it sends a heartbeat about once a minute. When no heartbeat has
arrived for HEARTBEAT_TIMEOUT, the shift ends at its last heartbeat, so the minutes nobody was
there aren't counted.

Shifts started somewhere else (the kiosk, the check-in monitor) are left alone: the API can
report on them and end them, but never times them out.
"""
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from base.models import Profile
from jobs.models import Role, Shift

from .models import ShiftHeartbeat

HEARTBEAT_TIMEOUT = timedelta(minutes=5)
HEARTBEAT_INTERVAL = timedelta(minutes=1)  # what clients are told to aim for
CHECK_EVERY = 60  # seconds between stale-shift passes run from middleware


class APIError(Exception):
    """A refusal the API reports as {"error": {"code", "message"}} with this HTTP status."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def find_person(town_hall_id):
    code = ''.join(ch for ch in str(town_hall_id or '') if ch.isdigit())
    profile = Profile.objects.filter(id_code=code, user__is_active=True).select_related('user').first() if len(code) == 6 else None
    if not profile:
        raise APIError(404, 'person_not_found', 'No active person has that Town Hall ID.')
    return profile.user


def find_role(api_key, role_id):
    role = Role.objects.filter(pk=role_id).first() if str(role_id or '').isdigit() else None
    if not role:
        raise APIError(404, 'role_not_found', 'No role has that ID.')
    if not role.permanent:
        raise APIError(400, 'role_not_permanent', 'Shifts can only be started through the API in permanent roles.')
    if not api_key.allows_role(role):
        raise APIError(403, 'role_not_allowed', 'This API key may not be used for that role.')
    return role


def open_shift(user):
    return Shift.objects.filter(user=user, end_time__isnull=True).select_related('role', 'heartbeat').first()


def _heartbeat_of(shift):
    try:
        return shift.heartbeat
    except ShiftHeartbeat.DoesNotExist:
        return None


def _check_role(shift, role):
    if shift.role_id != role.pk or shift.event_role_slot_id:
        raise APIError(409, 'other_shift_in_progress', 'This person is already on a shift in another role.')


@transaction.atomic
def start(api_key, user, role):
    """Start (or keep) the person's shift in this role. Returns (shift, started)."""
    end_stale_shifts()
    if not role.is_assigned_to(user):
        raise APIError(403, 'not_assigned', 'This person is not assigned to that permanent role.')
    # Lock the person's row so two heartbeats arriving together can't start two shifts
    Profile.objects.select_for_update().filter(user=user).first()
    shift = open_shift(user)
    if shift:
        _check_role(shift, role)
        heartbeat = _heartbeat_of(shift)
        if heartbeat:
            heartbeat.last_seen = timezone.now()
            heartbeat.save(update_fields=['last_seen'])
        return shift, False
    shift = Shift.objects.create(user=user, role=role)
    ShiftHeartbeat.objects.create(shift=shift, api_key=api_key, last_seen=shift.start_time)
    return shift, True


def heartbeat(api_key, user, role, start_if_needed=True):
    """The person is still working. Starts a new shift if theirs ended (unless told not to).
    Returns (shift or None, started)."""
    if start_if_needed:
        return start(api_key, user, role)
    end_stale_shifts()
    shift = open_shift(user)
    if not shift:
        return None, False
    _check_role(shift, role)
    beat = _heartbeat_of(shift)
    if beat:
        beat.last_seen = timezone.now()
        beat.save(update_fields=['last_seen'])
    return shift, False


@transaction.atomic
def end(user, role):
    """End the person's running shift in this role now. Returns the ended shift, or None."""
    end_stale_shifts()
    shift = open_shift(user)
    if not shift:
        return None
    _check_role(shift, role)
    ShiftHeartbeat.objects.filter(shift=shift).delete()
    shift.end_shift()
    return shift


def status(user, role):
    """The person's running shift in this role, or None."""
    end_stale_shifts()
    shift = open_shift(user)
    return shift if shift and shift.role_id == role.pk and not shift.event_role_slot_id else None


def end_stale_shifts(now=None):
    """End every API-tracked shift whose last heartbeat is older than HEARTBEAT_TIMEOUT.
    Each ends at its last heartbeat. Returns how many were ended."""
    cutoff = (now or timezone.now()) - HEARTBEAT_TIMEOUT
    ended = 0
    for beat in ShiftHeartbeat.objects.filter(last_seen__lt=cutoff).select_related('shift'):
        shift = beat.shift
        with transaction.atomic():
            # Claim it: only one worker gets to delete the row and end the shift
            if not ShiftHeartbeat.objects.filter(pk=beat.pk).delete()[0]:
                continue
            shift.refresh_from_db()
            if shift.end_time is None:
                shift.end_time = max(beat.last_seen, shift.start_time)
                shift.save()
                ended += 1
    return ended


def end_stale_if_idle():
    """Run end_stale_shifts at most every CHECK_EVERY seconds (called by middleware)."""
    if cache.add('api_stale_shift_check', True, CHECK_EVERY):
        try:
            end_stale_shifts()
        except Exception as exc:  # never break a page over this
            print(f'[api] ending stale shifts failed: {exc}')


def shift_data(shift, user=None):
    """The JSON shape of a shift."""
    if shift is None:
        return None
    user = user or shift.user
    beat = ShiftHeartbeat.objects.filter(shift=shift).first()
    end_time = shift.end_time
    minutes = ((end_time or timezone.now()) - shift.start_time).total_seconds() / 60
    return {
        'id': shift.pk,
        'town_hall_id': user.profile.id_code,
        'person': user.get_full_name() or user.username,
        'role': {'id': shift.role_id, 'name': shift.role.name if shift.role else None},
        'start_time': shift.start_time.isoformat(),
        'end_time': end_time.isoformat() if end_time else None,
        'active': end_time is None,
        'minutes': round(minutes, 1),
        'impact_points': shift.end_impact_points,
        'tracked_by_heartbeat': beat is not None,
        'last_heartbeat': beat.last_seen.isoformat() if beat else None,
    }
