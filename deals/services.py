"""Service layer.

Views stay thin: parse, delegate here, render. Everything that touches the
ORM and the ranking engine lives in this module, which keeps the search
pipeline testable without going through HTTP.
"""

from decimal import Decimal

from django.conf import settings
from django.db import transaction

from . import cache, ranking
from .models import Brand, Card, Deal, SearchRecord
from .seeds import dedupe_key
from .util import brand_key, jsonable, money


def _cards() -> list[Card]:
    return list(Card.objects.all())


def run_search(brand: str, amount: Decimal) -> dict:
    """DB reads + ranking. No history write, no cache — those wrap this."""
    key = brand_key(brand)
    cards = _cards()
    deals = list(Deal.objects.filter(brand_key=key))
    catalog = Brand.objects.filter(brand_key=key).first()

    results, excluded = ranking.rank_deals(amount, deals, cards) if deals else ([], [])

    if results:
        strategy = "deals"
        best_pick = results[0]
    else:
        # Either the brand has no deals, or every deal was disqualified by its
        # minimum spend. Both mean: recommend the best card for the amount.
        strategy = "best_card_fallback"
        best_pick = ranking.best_card_fallback(amount, catalog.category if catalog else None, cards)
    best_card = best_pick.get("payWith") if best_pick else None

    return {
        "query": {
            "brand": catalog.brand if catalog else brand,
            "brandKey": key,
            "amount": money(amount),
            "category": catalog.category if catalog else None,
            "currency": settings.DEALS_CURRENCY,
        },
        "count": len(results),
        "results": results,
        "bestPick": best_pick,
        "bestCard": best_card,
        "excluded": excluded,
        "strategy": strategy,
    }


def cached_search(brand: str, amount: Decimal) -> tuple[dict, bool]:
    """Returns (computed, cache_hit). Cached payloads are already JSON-safe."""
    key = cache.make_key(brand_key(brand), amount)
    hit = cache.get(key)
    if hit is not None:
        return hit, True
    computed = jsonable(run_search(brand, amount))
    cache.set(key, computed)
    return computed, False


def record_search(
    *, computed: dict, amount: Decimal, cache_hit: bool, latency_ms: float
) -> SearchRecord:
    best = computed.get("bestPick")
    return SearchRecord.objects.create(
        brand=computed["query"]["brand"],
        brand_key=computed["query"]["brandKey"],
        amount=money(amount),
        strategy=computed["strategy"],
        result_count=computed["count"],
        best_pick_title=best["title"] if best else None,
        best_pick_effective_price=money(best["effectivePrice"]) if best else None,
        cache_hit=cache_hit,
        latency_ms=latency_ms,
    )


@transaction.atomic
def create_deal(data: dict) -> tuple[Deal, bool]:
    """Insert a deal, or return the existing economically identical one.

    The UNIQUE dedupe_key is a second line of defence when a client retries
    without an Idempotency-Key: the retry is a no-op, not a duplicate offer.
    """
    key = dedupe_key(data["brand"], data["source"], data["discountPct"], data["minSpend"])
    deal, created = Deal.objects.get_or_create(
        dedupe_key=key,
        defaults={
            "brand": data["brand"],
            "brand_key": brand_key(data["brand"]),
            "title": data["title"],
            "source": data["source"],
            "discount_pct": data["discountPct"],
            "max_discount": data["maxDiscount"],
            "min_spend": data["minSpend"],
            "category": data["category"],
            "feeds": "api",
        },
    )
    # Keep the brand catalog in sync so the fallback can classify it later.
    Brand.objects.get_or_create(
        brand_key=brand_key(data["brand"]),
        defaults={"brand": data["brand"], "category": data["category"]},
    )
    if created:
        # A new deal changes the answer for that brand.
        cache.clear()
    return deal, created


def deal_payload(deal: Deal) -> dict:
    return {
        "id": deal.public_id,
        "brand": deal.brand,
        "title": deal.title,
        "source": deal.source,
        "discountPct": deal.discount_pct,
        "maxDiscount": deal.max_discount,
        "minSpend": deal.min_spend,
        "category": deal.category,
        "feeds": deal.feed_list,
    }


def history_payload(record: SearchRecord) -> dict:
    return {
        "id": record.public_id,
        "createdAt": record.created_at.isoformat(),
        "brand": record.brand,
        "amount": record.amount,
        "strategy": record.strategy,
        "resultCount": record.result_count,
        "bestPickTitle": record.best_pick_title,
        "bestPickEffectivePrice": record.best_pick_effective_price,
        "cacheHit": record.cache_hit,
        "latencyMs": record.latency_ms,
    }
