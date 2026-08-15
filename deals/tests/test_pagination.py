"""Cursor pagination on /searches, plus the two-feed merge."""

from django.test import SimpleTestCase

from deals.seeds import FEED_A, FEED_B, merge_feeds, merged_deals
from deals.util import CursorError, decode_cursor, encode_cursor

from .test_search import SeededAPITestCase


class PaginationTests(SeededAPITestCase):
    def setUp(self):
        super().setUp()
        for i in range(12):
            self.client.post("/deals/search", {"brand": "Zomato", "amount": 500 + i}, format="json")

    def page(self, **params):
        return self.client.get("/searches", params).json()

    def test_walks_every_row_exactly_once(self):
        seen, cursor, pages = [], None, 0
        while True:
            params = {"limit": 5}
            if cursor:
                params["cursor"] = cursor
            body = self.page(**params)
            seen.extend(item["id"] for item in body["items"])
            pages += 1
            cursor = body["pageInfo"]["nextCursor"]
            if not cursor:
                break
            self.assertLess(pages, 10, "pagination did not terminate")

        self.assertEqual(len(seen), 12)
        self.assertEqual(len(set(seen)), 12, "a row appeared on two pages")

    def test_pages_are_newest_first(self):
        amounts = [i["amount"] for i in self.page(limit=12)["items"]]
        self.assertEqual(amounts, sorted(amounts, reverse=True))

    def test_last_page_has_no_cursor(self):
        info = self.page(limit=100)["pageInfo"]
        self.assertFalse(info["hasMore"])
        self.assertIsNone(info["nextCursor"])

    def test_exact_multiple_of_limit_terminates_cleanly(self):
        """12 rows at limit 6: page two reports hasMore=false rather than
        handing out a cursor to an empty page three."""
        first = self.page(limit=6)
        self.assertTrue(first["pageInfo"]["hasMore"])
        second = self.page(limit=6, cursor=first["pageInfo"]["nextCursor"])
        self.assertEqual(len(second["items"]), 6)
        self.assertFalse(second["pageInfo"]["hasMore"])
        self.assertIsNone(second["pageInfo"]["nextCursor"])

    def test_rows_added_mid_pagination_do_not_shift_the_page(self):
        """Keyset, not OFFSET: new rows land ahead of the cursor."""
        first = self.page(limit=5)
        self.client.post("/deals/search", {"brand": "Swiggy", "amount": 4242}, format="json")
        second = self.page(limit=5, cursor=first["pageInfo"]["nextCursor"])

        overlap = {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
        self.assertEqual(overlap, set())
        self.assertTrue(all(i["amount"] != 4242 for i in second["items"]))

    def test_history_can_be_filtered_by_brand(self):
        self.client.post("/deals/search", {"brand": "Netflix", "amount": 999}, format="json")
        items = self.page(brand="netflix")["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["brand"], "Netflix")

    def test_history_records_the_ranking_outcome(self):
        item = self.page(limit=1)["items"][0]
        self.assertEqual(item["strategy"], "deals")
        self.assertTrue(item["bestPickTitle"])
        self.assertGreater(item["bestPickEffectivePrice"], 0)
        self.assertGreaterEqual(item["latencyMs"], 0)

    def test_empty_cursor_means_first_page(self):
        """A client that serialises a null cursor as "" gets page one, not 400."""
        body = self.page(limit=5, cursor="")
        self.assertEqual(len(body["items"]), 5)
        self.assertTrue(body["pageInfo"]["hasMore"])

    def test_malformed_cursor_is_rejected(self):
        for cursor in ["not-base64!!", "eyJmb28iOiJiYXIifQ", "MTIz", "e30="]:
            with self.subTest(cursor=cursor):
                response = self.client.get("/searches", {"cursor": cursor, "limit": 5})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "invalid_cursor")

    def test_invalid_limit_is_rejected(self):
        for limit in [0, -1, 1000, "many"]:
            with self.subTest(limit=limit):
                response = self.client.get("/searches", {"limit": limit})
                self.assertEqual(response.status_code, 422)

    def test_deals_endpoint_uses_the_same_pagination(self):
        body = self.client.get("/deals", {"limit": 10}).json()
        self.assertEqual(len(body["items"]), 10)
        self.assertTrue(body["pageInfo"]["hasMore"])


class CursorCodecTests(SimpleTestCase):
    def test_cursor_round_trips(self):
        self.assertEqual(decode_cursor(encode_cursor(97)), 97)

    def test_garbage_raises(self):
        with self.assertRaises(CursorError):
            decode_cursor("garbage")


class FeedMergeTests(SimpleTestCase):
    def test_merge_collapses_duplicate_offers_across_feeds(self):
        merged = merged_deals()
        keys = [d["dedupe_key"] for d in merged]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertLess(len(merged), len(FEED_A) + len(FEED_B))

    def test_merged_rows_record_every_feed_that_reported_them(self):
        both = [d for d in merged_deals() if len(d["feeds"]) > 1]
        self.assertTrue(both, "expected at least one deal present in both feeds")
        self.assertTrue(all(set(d["feeds"]) == {"partner_feed", "affiliate_feed"} for d in both))

    def test_conflicting_duplicate_keeps_the_better_cap(self):
        """SWIGGY30 is capped at 75 in one feed and 90 in the other: the
        user-favourable cap wins, and both feeds are still credited."""
        swiggy = next(
            d for d in merged_deals() if d["brand"] == "Swiggy" and d["discount_pct"] == 30
        )
        self.assertEqual(float(swiggy["max_discount"]), 90.0)
        self.assertEqual(set(swiggy["feeds"]), {"partner_feed", "affiliate_feed"})

    def test_uncapped_offer_beats_a_capped_duplicate(self):
        a = [("X", "capped", "offer", "10.00", "100.00", "0.00", "general")]
        b = [("X", "uncapped", "offer", "10.00", None, "0.00", "general")]
        merged = merge_feeds(("partner_feed", a), ("affiliate_feed", b))
        self.assertEqual(len(merged), 1)
        self.assertIsNone(merged[0]["max_discount"])


class SeededStoreTests(SeededAPITestCase):
    def test_store_holds_only_deduplicated_rows(self):
        self.assertEqual(self.client.get("/health").json()["deals"], len(merged_deals()))

    def test_seeding_twice_is_a_no_op(self):
        from deals.seeds import seed_database

        before = self.client.get("/health").json()["deals"]
        seed_database()
        self.assertEqual(self.client.get("/health").json()["deals"], before)
