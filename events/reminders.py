"""Day-before shift reminders.

Everyone signed up for a shift gets the "Shift reminder" email and push notification
(base/push.py) from 9 AM (the organization's time zone) the day before it starts: a
Friday 5 PM shift is reminded at 9 AM on Thursday. ``run_due_reminders`` sends whatever is due; ``ShiftReminderMiddleware``
runs it at most every few minutes as requests come in, so it works without a scheduler,
and ``manage.py send_shift_reminders`` runs it by hand. A pass that runs late still sends
until the shift starts.

People who sign up after their reminder time are skipped: the sign-up confirmation they
just got already has the details. A ``ShiftReminder`` row is created before each email,
so nobody is reminded twice about the same shift, even with several workers.

After a volunteer clocks out of their last shift at an event (kiosk or check-in
monitor), ``send_shift_thanks`` emails them a thank-you that links to their post-event
summary and its feedback form. A ``ShiftThanks`` row keeps it to one per event.
"""
from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import formats, timezone, translation

CHECK_EVERY = 300  # seconds between middleware passes
SEND_AT = time(9)  # local time, the day before the shift


def reminder_time(start):
    """When the reminder for a shift starting at ``start`` is due."""
    day_before = timezone.localtime(start).date() - timedelta(days=1)
    return timezone.make_aware(datetime.combine(day_before, SEND_AT))


def run_due_reminders(base_url, now=None):
    """Send the shift reminders that are due. ``base_url`` (scheme and host) makes the
    link absolute. Returns how many were sent."""
    from base import push, triggers
    from base.email import notify

    from .models import EventRoleSlot, ShiftReminder, SlotSignup

    if not triggers.setting('shift_reminder')[0] and not push.is_on('shift_reminder'):
        return 0  # switched off: nobody is marked, so switching it on still reaches upcoming shifts
    now = now or timezone.now()
    # A reminder is due at most 39 hours before its shift (9 AM the day before an 11:59 PM start).
    slots = [slot for slot in (EventRoleSlot.objects
                               .filter(start_time__gt=now, start_time__lte=now + timedelta(days=2),
                                       event__published=True, signups__isnull=False)
                               .distinct()
                               .select_related('event__venue', 'role')
                               .prefetch_related('signups__profile'))
             if reminder_time(slot.start_time) <= now]
    if not slots:
        return 0
    done = set(ShiftReminder.objects.filter(slot__in=slots).values_list('slot_id', 'user_id'))
    signed_up_at = dict(((s, u), at) for s, u, at in SlotSignup.objects.filter(slot__in=slots)
                        .values_list('slot_id', 'user_id', 'created_at'))

    sent = 0
    for slot in slots:
        due = reminder_time(slot.start_time)
        for user in slot.signups.all():
            if (slot.pk, user.pk) in done or not user.is_active:
                continue
            joined = signed_up_at.get((slot.pk, user.pk))
            if joined and joined >= due:
                continue
            try:
                with transaction.atomic():
                    ShiftReminder.objects.create(slot=slot, user=user)
            except IntegrityError:
                continue  # another worker got there first
            notify('shift_reminder', user, reminder_data(slot, user, base_url))
            sent += 1
    return sent


def reminder_data(slot, user, base_url):
    from base.utils import short_datetime

    event = slot.event
    profile = getattr(user, 'profile', None)
    lang = profile.language if profile and profile.language in dict(settings.LANGUAGES) else settings.LANGUAGE_CODE
    with translation.override(lang):  # the link's language prefix
        link = base_url.rstrip('/') + reverse('opportunity_detail', args=[event.pk])
    location = event.full_location + (f' ({event.report_to_location})' if event.report_to_location else '')
    return {
        'event': event.title, 'role': slot.role.name, 'date': short_datetime(slot.start_time, lang),
        'location': location, 'link': link,
    }


def send_shift_thanks(shift, base_url):
    """Thank a volunteer who just clocked out of an event shift ("Thank you after a
    shift"). Skipped while they have another shift at the event still to start, so
    someone working two shifts hears from us once, at the end. Returns True if sent."""
    from base import push, triggers
    from base.email import notify

    from .models import ShiftThanks

    slot = shift.event_role_slot
    user = shift.user
    if not slot or not shift.end_time or not (triggers.setting('shift_thanks')[0] or push.is_on('shift_thanks')):
        return False
    event = slot.event
    if not event.published or not user.is_active:
        return False
    if event.role_slots.filter(signups=user, start_time__gt=shift.end_time).exclude(pk=slot.pk).exists():
        return False  # coming back for another shift
    try:
        with transaction.atomic():
            ShiftThanks.objects.create(event=event, user=user)
    except IntegrityError:
        return False  # already thanked for this event
    profile = getattr(user, 'profile', None)
    lang = profile.language if profile and profile.language in dict(settings.LANGUAGES) else settings.LANGUAGE_CODE
    with translation.override(lang):  # the link's language prefix and decimal mark
        link = base_url.rstrip('/') + reverse('event_thank_you', args=[event.pk])
        hours = formats.number_format(f'{round(shift.duration(), 1):g}')
    notify('shift_thanks', user, {
        'event': event.title, 'role': slot.role.name, 'hours': hours,
        'points': shift.end_impact_points or 0,
        'link': link, 'feedback_link': f'{link}#feedback',
    })
    return True


def run_due_if_idle(request):
    """Run the reminder pass at most every CHECK_EVERY seconds (called by middleware)."""
    if cache.add('shift_reminders_due_check', True, CHECK_EVERY):
        try:
            run_due_reminders(request.build_absolute_uri('/'))
        except Exception as exc:  # never break a page over this
            print(f'[shift reminders] failed: {exc}')
