"""Idempotency on the write endpoints: POST /deals/search and POST /deals."""

from deals.models import Deal, IdempotencyRecord, SearchRecord

from .test_search import SeededAPITestCase

SEARCH = {"brand": "Zomato", "amount": 1200}
DEAL = {
    "brand": "NewBrand",
    "title": "25% launch offer",
    "source": "offer",
    "discountPct": 25,
    "maxDiscount": 500,
    "minSpend": 0,
    "category": "shopping",
}


class SearchIdempotencyTests(SeededAPITestCase):
    def post_search(self, key=None, payload=None):
        extra = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
        return self.client.post("/deals/search", payload or SEARCH, format="json", **extra)

    def test_replay_returns_the_original_response(self):
        first = self.post_search("search-key-1").json()
        second = self.post_search("search-key-1")

        self.assertEqual(second["Idempotency-Replayed"], "true")
        body = second.json()
        self.assertEqual(body["meta"]["searchId"], first["meta"]["searchId"])
        self.assertTrue(body["meta"]["idempotencyReplayed"])
        self.assertEqual(body["results"], first["results"])

    def test_replay_does_not_write_a_second_history_row(self):
        self.post_search("search-key-2")
        for _ in range(3):
            self.post_search("search-key-2")
        self.assertEqual(SearchRecord.objects.count(), 1)

    def test_searches_without_a_key_are_each_recorded(self):
        self.post_search()
        self.post_search()
        self.assertEqual(SearchRecord.objects.count(), 2)

    def test_same_key_with_a_different_body_is_a_conflict(self):
        self.post_search("search-key-3")
        response = self.post_search("search-key-3", {"brand": "Swiggy", "amount": 900})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "idempotency_conflict")
        self.assertEqual(SearchRecord.objects.count(), 1)

    def test_different_keys_are_independent(self):
        a = self.post_search("a").json()
        b = self.post_search("b").json()
        self.assertNotEqual(a["meta"]["searchId"], b["meta"]["searchId"])

    def test_keys_are_scoped_per_endpoint(self):
        """The same key on a different endpoint must not collide."""
        self.post_search("shared-key")
        response = self.client.post(
            "/deals", DEAL, format="json", HTTP_IDEMPOTENCY_KEY="shared-key"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(IdempotencyRecord.objects.filter(key="shared-key").count(), 2)

    def test_blank_or_oversized_key_is_rejected(self):
        for bad in ["   ", "k" * 500]:
            with self.subTest(key=bad[:10]):
                response = self.post_search(bad)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "invalid_idempotency_key")


class DealCreationIdempotencyTests(SeededAPITestCase):
    def test_replayed_creation_creates_one_row(self):
        first = self.client.post("/deals", DEAL, format="json", HTTP_IDEMPOTENCY_KEY="deal-1")
        second = self.client.post("/deals", DEAL, format="json", HTTP_IDEMPOTENCY_KEY="deal-1")

        self.assertEqual(first.status_code, 201)
        self.assertTrue(first.json()["created"])
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second["Idempotency-Replayed"], "true")
        self.assertEqual(second.json()["deal"]["id"], first.json()["deal"]["id"])
        self.assertEqual(Deal.objects.filter(brand="NewBrand").count(), 1)

    def test_duplicate_without_a_key_is_not_inserted_twice(self):
        """The unique dedupe_key is a second line of defence when a client
        retries without an Idempotency-Key."""
        first = self.client.post("/deals", DEAL, format="json")
        second = self.client.post("/deals", DEAL, format="json")

        self.assertTrue(first.json()["created"])
        self.assertFalse(second.json()["created"])
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["deal"]["id"], first.json()["deal"]["id"])
        self.assertEqual(Deal.objects.filter(brand="NewBrand").count(), 1)

    def test_created_deal_is_searchable_and_registers_its_brand(self):
        self.client.post("/deals", DEAL, format="json", HTTP_IDEMPOTENCY_KEY="deal-2")
        body = self.client.post(
            "/deals/search", {"brand": "newbrand", "amount": 2000}, format="json"
        ).json()
        self.assertEqual(body["meta"]["strategy"], "deals")
        self.assertEqual(body["query"]["category"], "shopping")
        # 25% of 2000 = 500, exactly at the cap.
        self.assertEqual(body["bestPick"]["price"], 1500.0)

    def test_invalid_deal_payloads_are_rejected(self):
        bad = [
            ("discount over 100", {**DEAL, "discountPct": 150}),
            ("unknown source", {**DEAL, "source": "magic_beans"}),
            ("negative min spend", {**DEAL, "minSpend": -1}),
            ("blank title", {**DEAL, "title": ""}),
        ]
        for label, payload in bad:
            with self.subTest(case=label):
                response = self.client.post("/deals", payload, format="json")
                self.assertEqual(response.status_code, 422)
        self.assertEqual(Deal.objects.filter(brand="NewBrand").count(), 0)
