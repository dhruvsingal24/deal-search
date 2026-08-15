"""Adds a measured server-side latency header to every response."""

import time


class LatencyHeaderMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.perf_counter()
        response = self.get_response(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response["X-Response-Time-Ms"] = f"{elapsed_ms:.3f}"
        return response
