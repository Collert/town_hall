"""Languages and time zone chosen at Console > Organization > Region.

They're stored on SiteSettings and applied once at startup, before modeltranslation and the
console forms read settings.LANGUAGES, so a change takes effect after the app restarts.
"""
import os
import signal
import sys
import threading
import time
import uuid
import warnings
import zoneinfo

from django.apps import AppConfig
from django.conf import settings
from django.db import DatabaseError, connection

# Changes every time the app starts; the console compares it to tell that a restart finished.
BOOT_ID = uuid.uuid4().hex

# What the settings file says, kept so a saved "use the default" choice can be resolved later.
SETTINGS_LANGUAGE_CODE = settings.LANGUAGE_CODE
SETTINGS_TIME_ZONE = settings.TIME_ZONE


def resolve(languages, default_language, time_zone):
    """(LANGUAGES, LANGUAGE_CODE, TIME_ZONE) for a saved choice. Unknown values fall back to the
    settings file: no valid language keeps them all, and the default language is listed first."""
    names = dict(settings.SUPPORTED_LANGUAGES)
    codes = [code for code in names if code in languages] or list(names)
    default = next((c for c in (default_language, SETTINGS_LANGUAGE_CODE) if c in codes), codes[0])
    ordered = [default] + [code for code in codes if code != default]
    if time_zone not in zoneinfo.available_timezones():
        time_zone = SETTINGS_TIME_ZONE
    return [(code, names[code]) for code in ordered], default, time_zone


def saved(site):
    """resolve() for what is saved on a SiteSettings row."""
    return resolve(site.language_codes(), site.default_language, site.time_zone)


def running():
    """What this process started with, in the same shape as resolve()."""
    return list(settings.LANGUAGES), settings.LANGUAGE_CODE, settings.TIME_ZONE


def restart_pending(site):
    """Whether the saved choice differs from what this process is running with."""
    def key(languages, default, time_zone):
        return [code for code, _name in languages], default, time_zone
    return key(*saved(site)) != key(*running())


def restart_method(request):
    """How this process can restart itself: 'gunicorn', 'runserver', or None."""
    if request.META.get('SERVER_SOFTWARE', '').startswith('gunicorn') and hasattr(signal, 'SIGHUP'):
        return 'gunicorn'
    if os.environ.get('RUN_MAIN') == 'true':  # runserver with its autoreloader
        return 'runserver'
    return None


def restart(method, delay=1.0):
    """Restart the app after `delay` seconds, so the current response goes out first."""
    def go():
        if method == 'gunicorn':
            os.kill(os.getppid(), signal.SIGHUP)  # the gunicorn master replaces every worker gracefully
        else:
            os.utime(__file__)  # the autoreloader restarts when a source file changes
    timer = threading.Timer(delay, go)
    timer.daemon = True
    timer.start()


def apply(languages, default, time_zone):
    settings.LANGUAGES = languages
    settings.LANGUAGE_CODE = default
    settings.TIME_ZONE = time_zone
    # Model fields fall back to the default language first, then the other switched-on ones,
    # then translations kept for languages that are switched off.
    codes = [code for code, _name in languages]
    settings.MODELTRANSLATION_DEFAULT_LANGUAGE = default
    settings.MODELTRANSLATION_FALLBACK_LANGUAGES = tuple(
        codes + [code for code in settings.MODELTRANSLATION_LANGUAGES if code not in codes]
    )
    if hasattr(time, 'tzset'):  # what Django does with TIME_ZONE when settings load (not on Windows)
        os.environ['TZ'] = time_zone
        time.tzset()

    from django.utils import timezone
    from django.utils.translation import trans_real
    for cached in (trans_real.get_languages, trans_real.check_for_language,
                   trans_real.get_supported_language_variant, timezone.get_default_timezone):
        cached.cache_clear()


class SiteConfig(AppConfig):
    name = 'town_hall'
    verbose_name = 'Site configuration'

    def ready(self):
        if sys.argv[1:2] == ['test']:  # tests always run with every language and the settings file's time zone
            return
        from base.models import SiteSettings
        try:
            with warnings.catch_warnings():  # reading settings once at startup is the point here
                warnings.simplefilter('ignore', RuntimeWarning)
                site = SiteSettings.objects.only('languages', 'default_language', 'time_zone').filter(pk=1).first()
        except DatabaseError:  # fresh database, or these columns aren't migrated yet
            site = None
        finally:
            connection.close()
        if site:
            apply(*saved(site))
