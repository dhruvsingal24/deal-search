"""The HTML frontend.

Deliberately server-rendered from a GET query string rather than a JS client
posting to the API:

  * A GET form needs no CSRF token, so the API can stay token-less and
    `CsrfViewMiddleware` stays out of the stack.
  * Results are linkable and shareable — /?brand=Zomato&amount=1200 is the
    whole state.
  * It works with JavaScript disabled.

It goes through the same `cached_search` + `record_search` path as
`POST /deals/search`, so a UI search caches and records history exactly like an
API search. The page is a second interface to one pipeline, not a fork of it.
"""

import time

from django.shortcuts import render

from . import services
from .models import SearchRecord
from .serializers import SearchRequestSerializer

EXAMPLES = [
    ("Zomato", 1200, "several deals, capped discounts"),
    ("Amazon", 8000, "biggest headline discount loses"),
    ("BigBasket", 100, "all deals below minimum spend"),
    ("IndiGo", 25000, "no deals — best-card fallback"),
]


def search_page(request):
    brand = (request.GET.get("brand") or "").strip()
    amount = (request.GET.get("amount") or "").strip()

    context = {
        "brand": brand,
        "amount": amount,
        "examples": EXAMPLES,
        "submitted": bool(brand or amount),
    }

    if brand or amount:
        serializer = SearchRequestSerializer(data={"brand": brand, "amount": amount})
        if serializer.is_valid():
            started = time.perf_counter()
            validated = serializer.validated_data
            computed, cache_hit = services.cached_search(validated["brand"], validated["amount"])
            latency_ms = round((time.perf_counter() - started) * 1000, 3)
            record = services.record_search(
                computed=computed,
                amount=validated["amount"],
                cache_hit=cache_hit,
                latency_ms=latency_ms,
            )
            context.update(
                {
                    "result": computed,
                    "meta": {
                        "searchId": record.public_id,
                        "strategy": computed["strategy"],
                        "cacheHit": cache_hit,
                        "latencyMs": latency_ms,
                    },
                }
            )
        else:
            # DRF gives {field: [messages]}; flatten for display.
            context["errors"] = {
                field: " ".join(str(m) for m in messages)
                for field, messages in serializer.errors.items()
            }

    context["recent"] = SearchRecord.objects.order_by("-pk")[:8]
    return render(request, "deals/search.html", context)
