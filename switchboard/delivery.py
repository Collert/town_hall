"""Delivering events to Switchboard: signed POSTs, retries and the delivery log.

Each `Delivery` row is tried up to MAX_ATTEMPTS times with growing waits. The first try runs
in a thread right after the transaction commits; a retry is a timer in that process, and
`run_due()` (throttled middleware, or `manage.py send_switchboard_deliveries`) picks up any a
restart left behind. An attempt first claims its row with a conditional update, so two
workers never send the same try twice.

All outgoing HTTP goes through `_http`, which is what tests replace.
"""
import hashlib
import hmac
import logging
import threading
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)

TIMEOUT = 10
USER_AGENT = 'TownHall/1.0 (Switchboard)'
MAX_ATTEMPTS = 5
RETRY_WAITS = [5, 30, 120, 600]  # seconds after the 1st, 2nd, 3rd and 4th failed try
LEASE = timedelta(minutes=2)     # how long a try may take before run_due() treats it as lost
KEEP_LOG = timedelta(days=14)
CHECK_EVERY = 30                 # seconds between run_due() passes from middleware


class DeliveryError(Exception):
    """Switchboard couldn't be reached at all (network error or timeout)."""


def _http(url, body, headers):
    """POST `body` (bytes) and return the HTTP status. Raises DeliveryError when no answer came."""
    request = urllib.request.Request(url, data=body, method='POST', headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise DeliveryError(str(getattr(exc, 'reason', exc))[:300]) from exc


def sign(secret, body):
    return 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _inline():
    return getattr(settings, 'SWITCHBOARD_RUN_INLINE', False)


def run_after_commit(job, *args):
    """Run ``job(*args)`` in a thread once the current transaction commits. Failures are logged.
    With settings.SWITCHBOARD_RUN_INLINE (tests) it runs in the commit hook."""
    from django.db import transaction

    if _inline():
        transaction.on_commit(lambda: _run(job, *args))
    else:
        transaction.on_commit(lambda: threading.Thread(target=_run, args=(job, *args), daemon=True).start())


def _run(job, *args):
    from django.db import connection
    try:
        job(*args)
    except Exception:
        logger.exception('Switchboard: %s failed', getattr(job, '__name__', job))
    finally:
        if not _inline():
            connection.close()


def send(delivery_ids):
    for delivery_id in delivery_ids:
        attempt(delivery_id)


def attempt(delivery_id, now=None):
    """Try one delivery once, if it is due, and record how it went. Returns its status afterwards."""
    from .models import Delivery, SwitchboardConnection

    delivery = Delivery.objects.select_related('connection').filter(pk=delivery_id).first()
    if not delivery or delivery.status != Delivery.PENDING:
        return delivery.status if delivery else None
    now = now or timezone.now()
    # Claim this try: only one worker's update matches, and the lease keeps the others away
    # until the answer is recorded.
    claimed = Delivery.objects.filter(
        pk=delivery.pk, status=Delivery.PENDING, attempts=delivery.attempts, next_attempt_at__lte=now,
    ).update(attempts=delivery.attempts + 1, next_attempt_at=timezone.now() + LEASE)
    if not claimed:
        return Delivery.PENDING  # not due yet, or another worker has this try
    delivery.attempts += 1
    connection = delivery.connection
    body = delivery.body.encode()
    status, error = None, ''
    try:
        status = _http(connection.webhook_url, body, {
            'Content-Type': 'application/json',
            'User-Agent': USER_AGENT,
            'X-Switchboard-Event': delivery.event_type,
            'X-Switchboard-Delivery': delivery.event_id,
            'X-Switchboard-Signature': sign(connection.webhook_secret, body),
        })
    except DeliveryError as exc:
        error = str(exc) or 'No answer'
    except Exception as exc:  # anything else counts as a failed try, never a crash
        error = f'{type(exc).__name__}: {exc}'[:300]

    now = timezone.now()
    connections = SwitchboardConnection.objects.filter(pk=connection.pk)
    delivery.response_status = status
    if status is not None and 200 <= status < 300:
        delivery.status, delivery.error, delivery.body = Delivery.DELIVERED, '', ''
        delivery.next_attempt_at, delivery.finished_at = None, now
        connections.update(last_delivery_at=now, failures=0, last_error='')
    else:
        delivery.error = error or f'Switchboard answered {status}'
        connections.update(failures=F('failures') + 1, last_error=delivery.error)
        if status == 410:
            # Switchboard no longer knows this connection: stop, and say so on the Backend page.
            delivery.status, delivery.body = Delivery.FAILED, ''
            delivery.next_attempt_at, delivery.finished_at = None, now
            connections.update(broken_at=now)
            Delivery.objects.filter(connection=connection, status=Delivery.PENDING).exclude(pk=delivery.pk).update(
                status=Delivery.FAILED, body='', next_attempt_at=None, finished_at=now, error=delivery.error)
        elif delivery.attempts >= MAX_ATTEMPTS:
            delivery.status, delivery.body = Delivery.FAILED, ''
            delivery.next_attempt_at, delivery.finished_at = None, now
        else:
            wait = RETRY_WAITS[min(delivery.attempts, len(RETRY_WAITS)) - 1]
            delivery.next_attempt_at = now + timedelta(seconds=wait)
            if not _inline():
                timer = threading.Timer(wait, _run, args=(attempt, delivery.pk))
                timer.daemon = True
                timer.start()
    delivery.save(update_fields=['status', 'error', 'body', 'response_status', 'next_attempt_at', 'finished_at'])
    return delivery.status


def run_due(now=None):
    """Try every pending delivery whose time has come. Returns how many were tried."""
    from .models import Delivery

    now = now or timezone.now()
    due = list(Delivery.objects.filter(status=Delivery.PENDING, next_attempt_at__lte=now)
               .order_by('next_attempt_at').values_list('pk', flat=True)[:200])
    for delivery_id in due:
        attempt(delivery_id, now)
    return len(due)


def prune(now=None):
    """Drop the delivery log, stored action answers and used codes after two weeks."""
    from .models import AuthorizeCode, Delivery, IdempotencyRecord

    cutoff = (now or timezone.now()) - KEEP_LOG
    Delivery.objects.filter(created_at__lt=cutoff).exclude(status=Delivery.PENDING).delete()
    IdempotencyRecord.objects.filter(created_at__lt=cutoff).delete()
    AuthorizeCode.objects.filter(expires_at__lt=cutoff).delete()


def run_due_if_idle():
    """From middleware: at most every CHECK_EVERY seconds, send what is overdue in a thread
    (retries whose timer died with a restart) and prune once a day."""
    if not cache.add('switchboard_delivery_check', True, CHECK_EVERY):
        return
    try:
        from .models import Delivery
        # Overdue by more than a timer's slack, so this doesn't race the timers for no reason.
        late = timezone.now() - timedelta(seconds=10)
        if Delivery.objects.filter(status=Delivery.PENDING, next_attempt_at__lte=late).exists():
            threading.Thread(target=_run, args=(run_due, late), daemon=True).start()
        if cache.add('switchboard_prune', True, 24 * 3600):
            prune()
    except Exception:  # never break a page over this
        logger.exception('Switchboard: checking for due deliveries failed')
