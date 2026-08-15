"""API views. Thin: validate, delegate to services, render."""

import time

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import cache, pagination, services
from .idempotency import Idempotency, normalise_key
from .models import Card, Deal, SearchRecord
from .serializers import CreateDealSerializer, HistoryQuerySerializer, SearchRequestSerializer
from .util import brand_key, jsonable


class DealSearchView(APIView):
    """POST /deals/search — the cheapest way to pay a brand.

    This is a write endpoint: it appends to search history. Hence the
    Idempotency-Key support.
    """

    endpoint = "POST /deals/search"

    def post(self, request):
        started = time.perf_counter()

        serializer = SearchRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        brand, amount = payload["brand"], payload["amount"]

        idem = Idempotency(
            normalise_key(request.headers.get("Idempotency-Key")),
            self.endpoint,
            jsonable(payload),
        )
        replay = idem.replay()
        if replay:
            _, body = replay
            body.setdefault("meta", {})["idempotencyReplayed"] = True
            response = Response(body)
            response["Idempotency-Replayed"] = "true"
            return response

        computed, cache_hit = services.cached_search(brand, amount)
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        record = services.record_search(
            computed=computed, amount=amount, cache_hit=cache_hit, latency_ms=latency_ms
        )

        body = jsonable(
            {
                "query": computed["query"],
                "count": computed["count"],
                "results": computed["results"],
                "bestPick": computed["bestPick"],
                "bestCard": computed["bestCard"],
                "excluded": computed["excluded"],
                "meta": {
                    "searchId": record.public_id,
                    "strategy": computed["strategy"],
                    "cacheHit": cache_hit,
                    "latencyMs": latency_ms,
                    "idempotencyReplayed": False,
                },
            }
        )
        idem.remember(status.HTTP_200_OK, body)

        response = Response(body)
        response["X-Cache"] = "HIT" if cache_hit else "MISS"
        return response


class DealListCreateView(APIView):
    """POST /deals — add a deal. GET /deals — list them, cursor-paginated."""

    endpoint = "POST /deals"

    def get(self, request):
        query = HistoryQuerySerializer(data=request.query_params.dict())
        query.is_valid(raise_exception=True)
        params = query.validated_data

        queryset = Deal.objects.order_by("-pk")
        if params["brand"]:
            queryset = queryset.filter(brand_key=brand_key(params["brand"]))
        page, page_info = pagination.paginate(queryset, params["limit"], params["cursor"])
        return Response(
            jsonable(
                {
                    "items": [services.deal_payload(d) for d in page],
                    "pageInfo": page_info,
                }
            )
        )

    def post(self, request):
        serializer = CreateDealSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        idem = Idempotency(
            normalise_key(request.headers.get("Idempotency-Key")),
            self.endpoint,
            jsonable(payload),
        )
        replay = idem.replay()
        if replay:
            code, body = replay
            response = Response(body, status=code)
            response["Idempotency-Replayed"] = "true"
            return response

        deal, created = services.create_deal(payload)
        body = jsonable({"created": created, "deal": services.deal_payload(deal)})
        code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        idem.remember(code, body)
        return Response(body, status=code)


class SearchHistoryView(APIView):
    """GET /searches — cursor-paginated history, newest first."""

    def get(self, request):
        query = HistoryQuerySerializer(data=request.query_params.dict())
        query.is_valid(raise_exception=True)
        params = query.validated_data

        queryset = SearchRecord.objects.order_by("-pk")
        if params["brand"]:
            queryset = queryset.filter(brand_key=brand_key(params["brand"]))
        page, page_info = pagination.paginate(queryset, params["limit"], params["cursor"])
        return Response(
            jsonable(
                {
                    "items": [services.history_payload(r) for r in page],
                    "pageInfo": page_info,
                }
            )
        )


class CardListView(APIView):
    def get(self, request):
        cards = Card.objects.all()
        return Response(
            jsonable(
                {
                    "count": cards.count(),
                    "cards": [
                        {
                            "id": c.id,
                            "name": c.name,
                            "network": c.network,
                            "baseRate": c.base_rate,
                            "bonusCategory": c.bonus_category,
                            "bonusRate": c.bonus_rate,
                            "rewardCap": c.reward_cap,
                            "annualFee": c.annual_fee,
                        }
                        for c in cards
                    ],
                }
            )
        )


def _counts() -> dict:
    return {
        "deals": Deal.objects.count(),
        "cards": Card.objects.count(),
        "searches": SearchRecord.objects.count(),
    }


class HealthView(APIView):
    def get(self, request):
        return Response({"status": "ok", "version": "1.0.0", **_counts()})


class StatsView(APIView):
    def get(self, request):
        return Response(
            {
                "store": _counts(),
                "cache": {
                    **cache.stats.as_dict(),
                    "ttlSeconds": settings.CACHES["default"]["TIMEOUT"],
                    "maxEntries": settings.CACHES["default"]["OPTIONS"]["MAX_ENTRIES"],
                },
            }
        )


class CacheView(APIView):
    def delete(self, request):
        cache.flush_all()
        return Response({"flushed": True, "cache": cache.stats.as_dict()})
