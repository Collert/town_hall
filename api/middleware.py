class StaleShiftMiddleware:
    """Ends API-tracked shifts whose heartbeats stopped, checking at most every minute as
    requests come in, so it works without a scheduler (api/shifts.py). The API endpoints also
    check before every call, and `manage.py end_stale_shifts` runs it by hand."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .shifts import end_stale_if_idle
        end_stale_if_idle()
        return self.get_response(request)
