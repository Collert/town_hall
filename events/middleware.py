class LeadDeadlineMiddleware:
    """Fills open shift lead positions 48 hours before events start, checking at most every
    few minutes as requests come in, so it works without a scheduler (events/leadership.py)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .leadership import run_due_if_idle
        run_due_if_idle()
        return self.get_response(request)
