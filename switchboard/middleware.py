class SwitchboardDeliveryMiddleware:
    """Sends Switchboard deliveries whose retry is overdue (for example after a restart) and
    prunes the delivery log, checking at most every 30 seconds as requests come in, so it
    works without a scheduler (switchboard/delivery.py); and, at most every 5 minutes, emits
    the events that have come due with time, such as "event starts within 24 hours"
    (switchboard/timed.py). `manage.py send_switchboard_deliveries` runs both by hand."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .delivery import run_due_if_idle
        from .timed import run_due_if_idle as run_timed_if_idle
        run_due_if_idle()
        run_timed_if_idle()
        return self.get_response(request)
