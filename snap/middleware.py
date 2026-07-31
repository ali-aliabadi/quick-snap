"""Request-level Prometheus instrumentation."""

import time

from .metrics import http_latency, http_requests


class MetricsMiddleware:
    """Count and time every request, labelled by resolved view name.

    The label is the Django *view name* (e.g. ``snap:capture``), never the raw
    path. Paths would let a scanner hitting /wp-login.php, /.env, /admin.php…
    mint a new label value per request and grow the metric series without
    bound; view names are drawn from a fixed set the URLconf defines.
    """

    # Never instrumented: /metrics itself (self-referential noise).
    SKIP = {"metrics"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.perf_counter()
        response = self.get_response(request)
        elapsed = time.perf_counter() - started

        view = self._view_name(request)
        if view in self.SKIP:
            return response

        # Status *class* (2xx/3xx/4xx/5xx) keeps cardinality low while still
        # answering "is anything failing".
        http_requests.labels(
            view=view,
            method=request.method,
            status=f"{response.status_code // 100}xx",
        ).inc()
        http_latency.labels(view=view).observe(elapsed)
        return response

    @staticmethod
    def _view_name(request):
        match = getattr(request, "resolver_match", None)
        if match and match.view_name:
            return match.view_name
        # Unrouted (404s, bad hosts) collapse into one bucket on purpose.
        return "<unmatched>"
