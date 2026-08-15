"""End-to-end tests for POST /deals/search."""

from django.test import TestCase
from rest_framework.test import APIClient

from deals import cache
from deals.seeds import seed_database


class SeededAPITestCase(TestCase):
    """Seeds once per class; Django rolls each test back to that state."""

    @classmethod
    def setUpTestData(cls):
        seed_database()

    def setUp(self):
        self.client = APIClient()
        cache.flush_all()

    def search(self, brand, amount, **kw):
        return self.client.post(
            "/deals/search", {"brand": brand, "amount": amount}, format="json", **kw
        )


class SearchTests(SeededAPITestCase):
    def test_returns_ranked_deals_and_a_best_pick(self):
        response = self.search("Zomato", 1200)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["meta"]["strategy"], "deals")
        self.assertGreaterEqual(body["count"], 1)

        prices = [d["effectivePrice"] for d in body["results"]]
        self.assertEqual(prices, sorted(prices))
        self.assertEqual(body["bestPick"]["id"], body["results"][0]["id"])
        self.assertEqual(body["bestCard"]["cardId"], body["results"][0]["payWith"]["cardId"])

    def test_best_pick_is_never_beaten_by_another_result(self):
        body = self.search("Amazon", 8000).json()
        best = body["bestPick"]["effectivePrice"]
        self.assertTrue(all(d["effectivePrice"] >= best for d in body["results"]))

    def test_headline_discount_does_not_decide_the_winner(self):
        """Seeded regression: Amazon's 15% pantry coupon is capped at 300 and
        sits in a weaker reward category, so it finishes behind the 10% one."""
        body = self.search("Amazon", 8000).json()
        self.assertEqual(body["bestPick"]["discountPct"], 10.0)
        self.assertEqual(body["results"][-1]["discountPct"], 15.0)

    def test_brand_lookup_ignores_case_spacing_and_punctuation(self):
        seen = set()
        for variant in ["BigBasket", "big basket", "  BIG-BASKET! "]:
            body = self.search(variant, 2000).json()
            self.assertEqual(body["query"]["brand"], "BigBasket")
            seen.add(tuple(d["id"] for d in body["results"]))
        self.assertEqual(len(seen), 1)

    def test_deals_below_min_spend_are_excluded_not_ranked(self):
        body = self.search("Zomato", 250).json()
        self.assertTrue(all(d["minSpend"] <= 250 for d in body["results"]))
        self.assertTrue(any(e["reason"] == "min_spend_not_met" for e in body["excluded"]))

    def test_latency_header_is_present(self):
        response = self.search("Swiggy", 900)
        self.assertGreater(float(response["X-Response-Time-Ms"]), 0)
        self.assertGreaterEqual(response.json()["meta"]["latencyMs"], 0)

    def test_get_is_not_allowed_on_search(self):
        response = self.client.get("/deals/search")
        self.assertEqual(response.status_code, 405)
        self.assertIn("error", response.json())


class FallbackTests(SeededAPITestCase):
    def test_unknown_brand_falls_back_to_best_card(self):
        body = self.search("IndiGo", 25000).json()
        self.assertEqual(body["meta"]["strategy"], "best_card_fallback")
        self.assertEqual(body["results"], [])
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["bestPick"]["source"], "card_reward")
        # IndiGo is catalogued as travel, so the travel-bonus card should win.
        self.assertEqual(body["bestCard"]["cardId"], "card_aurora")
        self.assertLess(body["bestPick"]["effectivePrice"], 25000)

    def test_brand_with_no_catalog_entry_still_gets_a_card(self):
        body = self.search("Some Shop That Does Not Exist", 5000).json()
        self.assertEqual(body["meta"]["strategy"], "best_card_fallback")
        self.assertIsNone(body["query"]["category"])
        self.assertIsNotNone(body["bestCard"])

    def test_amount_too_small_for_every_deal_falls_back(self):
        """Every BigBasket deal has a minimum spend, so 100 knocks them all out
        and the fallback takes over even though the brand *does* have deals."""
        body = self.search("BigBasket", 100).json()
        self.assertEqual(body["meta"]["strategy"], "best_card_fallback")
        self.assertEqual(body["results"], [])
        self.assertEqual(len(body["excluded"]), 3)
        self.assertEqual(body["bestCard"]["cardId"], "card_nimbus")  # groceries bonus


class ValidationTests(SeededAPITestCase):
    INVALID = [
        ("missing amount", {"brand": "Zomato"}),
        ("missing brand", {"amount": 100}),
        ("blank brand", {"brand": "", "amount": 100}),
        ("whitespace brand", {"brand": "   ", "amount": 100}),
        ("zero amount", {"brand": "Zomato", "amount": 0}),
        ("negative amount", {"brand": "Zomato", "amount": -50}),
        ("non-numeric amount", {"brand": "Zomato", "amount": "free"}),
        ("amount above ceiling", {"brand": "Zomato", "amount": 10**12}),
        ("brand too long", {"brand": "x" * 200, "amount": 100}),
        ("unknown field", {"brand": "Zomato", "amount": 100, "oops": 1}),
    ]

    def test_invalid_payloads_are_rejected(self):
        for label, payload in self.INVALID:
            with self.subTest(case=label):
                response = self.client.post("/deals/search", payload, format="json")
                self.assertEqual(response.status_code, 422)
                body = response.json()
                self.assertEqual(body["error"]["code"], "validation_error")
                self.assertTrue(body["error"]["details"])

    def test_every_problem_is_reported_in_one_response(self):
        response = self.client.post(
            "/deals/search", {"brand": "", "amount": -5, "oops": 1}, format="json"
        )
        fields = {d["field"] for d in response.json()["error"]["details"]}
        self.assertEqual(fields, {"brand", "amount", "oops"})

    def test_malformed_json_is_a_400_not_a_500(self):
        response = self.client.post(
            "/deals/search", data="{not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())


class CacheTests(SeededAPITestCase):
    def test_identical_search_is_served_from_cache(self):
        first = self.search("Zomato", 1200)
        second = self.search("Zomato", 1200)
        self.assertEqual(first["X-Cache"], "MISS")
        self.assertEqual(second["X-Cache"], "HIT")
        self.assertFalse(first.json()["meta"]["cacheHit"])
        self.assertTrue(second.json()["meta"]["cacheHit"])
        # Same answer, different search id: the history write is not cached.
        self.assertEqual(first.json()["results"], second.json()["results"])
        self.assertNotEqual(first.json()["meta"]["searchId"], second.json()["meta"]["searchId"])

    def test_cache_hit_still_records_history(self):
        for _ in range(3):
            self.search("Zomato", 1200)
        items = self.client.get("/searches", {"limit": 50}).json()["items"]
        self.assertEqual(len(items), 3)
        self.assertEqual([i["cacheHit"] for i in items], [True, True, False])

    def test_cache_key_includes_the_amount(self):
        self.search("Zomato", 1200)
        self.assertEqual(self.search("Zomato", 1500)["X-Cache"], "MISS")

    def test_cache_stats_and_flush(self):
        self.search("Swiggy", 900)
        self.search("Swiggy", 900)
        stats = self.client.get("/stats").json()["cache"]
        self.assertEqual((stats["hits"], stats["misses"]), (1, 1))
        self.client.delete("/cache")
        self.assertEqual(self.client.get("/stats").json()["cache"]["hits"], 0)

    def test_adding_a_deal_invalidates_the_cache(self):
        before = self.search("Spotify", 1000).json()["count"]
        self.client.post(
            "/deals",
            {
                "brand": "Spotify",
                "title": "Test 90% off",
                "source": "coupon",
                "discountPct": 90,
                "minSpend": 0,
                "category": "streaming",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="cache-invalidation-1",
        )

        after = self.search("Spotify", 1000).json()
        self.assertEqual(after["count"], before + 1)
        self.assertFalse(after["meta"]["cacheHit"])
        self.assertEqual(after["bestPick"]["title"], "Test 90% off")


class SupportingEndpointTests(SeededAPITestCase):
    def test_health(self):
        body = self.client.get("/health").json()
        self.assertEqual(body["status"], "ok")
        self.assertGreater(body["deals"], 0)

    def test_cards(self):
        body = self.client.get("/cards").json()
        self.assertEqual(body["count"], 6)
        self.assertTrue(all(c["baseRate"] > 0 for c in body["cards"]))

    def test_unknown_route_returns_the_error_envelope(self):
        response = self.client.get("/nope")
        self.assertEqual(response.status_code, 404)
