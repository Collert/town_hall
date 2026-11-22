class SwitchboardDeliveryMiddleware:
    """Sends Switchboard deliveries whose retry is overdue (for example after a restart) and
    prunes the delivery log, checking at most every 30 seconds as requests come in, so it
    works without a scheduler (switchboard/delivery.py). `manage.py
    send_switchboard_deliveries` runs it by hand."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .delivery import run_due_if_idle
        run_due_if_idle()
        return self.get_response(request)
