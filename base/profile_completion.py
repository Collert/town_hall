"""Complete-your-profile nudges.

A profile is complete once it has a photo (uploaded or Gravatar), a bio, at least one
skill and a location: what the public profile page shows. Completing it earns a one-time
award (``points.award_profile``).

People whose profile is still incomplete get the "Profile reminder" email a day after
they register and "Second profile reminder" a week after. ``run_due_reminders`` sends
whatever is due; ``ProfileReminderMiddleware`` runs it at most every few minutes as
requests come in, so it works without a scheduler, and ``manage.py
send_profile_reminders`` runs it by hand. ``Profile.profile_reminders_sent`` keeps
each email to once per person, even with several workers.
"""
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _

CHECK_EVERY = 300  # seconds between middleware passes

# (trigger, sent this long after registering), in order. ``profile_reminders_sent`` holds
# how many of them are done. Each is sent only within GRACE of being due, so switching the
# feature on (or a long quiet spell) doesn't email everyone who registered months ago.
REMINDERS = [
    ('profile_reminder', timedelta(days=1)),
    ('profile_reminder_week', timedelta(days=7)),
]
GRACE = timedelta(days=3)

ITEMS = {
    'photo': _('a profile photo'),
    'bio': _('a short bio'),
    'skills': _('your skills'),
    'location': _('your location'),
}


def missing(profile):
    """Keys of ITEMS the profile still lacks, in ITEMS order."""
    have = {
        'photo': bool(profile.avatar) or profile.use_gravatar,
        'bio': bool(profile.bio.strip()),
        'skills': profile.skills.exists(),
        'location': bool(profile.location.strip()),
    }
    return [key for key in ITEMS if not have[key]]


def is_complete(profile):
    return not missing(profile)


def run_due_reminders(base_url, now=None):
    """Send the reminder emails that are due. ``base_url`` (scheme and host) makes the
    link absolute. Returns how many were sent."""
    from . import triggers
    from .email import notify
    from .models import Profile

    now = now or timezone.now()
    sent = 0
    for step, (trigger, after) in enumerate(REMINDERS, start=1):
        if not triggers.setting(trigger)[0]:
            continue  # switched off: leave people unmarked so switching it on still reaches them
        due = (Profile.objects
               .filter(profile_reminders_sent__lt=step, user__is_active=True,
                       user__date_joined__lte=now - after, user__date_joined__gt=now - after - GRACE)
               .exclude(user__email='')
               .select_related('user'))
        for profile in due:
            gaps = missing(profile)
            if not gaps:
                continue
            # Claim the email first: only one worker wins when several run the pass at once.
            if not Profile.objects.filter(pk=profile.pk, profile_reminders_sent=profile.profile_reminders_sent).update(
                    profile_reminders_sent=step):
                continue
            notify(trigger, profile.user, reminder_data(profile, gaps, base_url))
            sent += 1
    return sent


def reminder_data(profile, gaps, base_url):
    from .models import PointsRules

    lang = profile.language if profile.language in dict(settings.LANGUAGES) else settings.LANGUAGE_CODE
    with translation.override(lang):  # the link and the list of missing items
        return {
            'missing': ', '.join(str(ITEMS[key]) for key in gaps),
            'points': PointsRules.get().profile_complete_points,
            'link': base_url.rstrip('/') + reverse('edit_profile'),
        }


def run_due_if_idle(request):
    """Run the reminder pass at most every CHECK_EVERY seconds (called by middleware)."""
    if cache.add('profile_reminders_due_check', True, CHECK_EVERY):
        try:
            run_due_reminders(request.build_absolute_uri('/'))
        except Exception as exc:  # never break a page over this
            print(f'[profile reminders] failed: {exc}')
