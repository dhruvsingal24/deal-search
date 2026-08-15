"""The HTML frontend and the serverless cold-start bootstrap."""

from django.test import SimpleTestCase, override_settings

from deals.models import SearchRecord

from .test_search import SeededAPITestCase


class SearchPageTests(SeededAPITestCase):
    def get(self, **params):
        return self.client.get("/", params)

    def test_landing_page_renders_without_a_query(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "deals/search.html")
        self.assertContains(response, "Try one of these")
        self.assertNotContains(response, "Best pick")

    def test_search_renders_ranked_results(self):
        response = self.get(brand="Amazon", amount=8000)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Best pick")
        self.assertContains(response, "AMZN10")

        # The page must rank the same way the API does: the 10% deal first,
        # the 15% one last.
        results = response.context["result"]["results"]
        self.assertEqual(float(results[0]["discountPct"]), 10.0)
        self.assertEqual(float(results[-1]["discountPct"]), 15.0)

    def test_page_uses_the_same_pipeline_as_the_api(self):
        page = self.get(brand="Zomato", amount=1200).context["result"]
        api = self.client.post(
            "/deals/search", {"brand": "Zomato", "amount": 1200}, format="json"
        ).json()
        self.assertEqual([d["id"] for d in page["results"]], [d["id"] for d in api["results"]])
        self.assertEqual(
            float(page["bestPick"]["effectivePrice"]), api["bestPick"]["effectivePrice"]
        )

    def test_page_search_is_recorded_in_history(self):
        self.get(brand="Swiggy", amount=900)
        record = SearchRecord.objects.latest("pk")
        self.assertEqual(record.brand, "Swiggy")
        self.assertEqual(record.strategy, "deals")

    def test_page_search_uses_the_cache(self):
        self.get(brand="Zomato", amount=1200)
        second = self.get(brand="Zomato", amount=1200)
        self.assertTrue(second.context["meta"]["cacheHit"])

    def test_fallback_is_rendered_differently(self):
        response = self.get(brand="IndiGo", amount=25000)
        self.assertContains(response, "No deal applies")
        self.assertContains(response, "Aurora Platinum")
        self.assertEqual(response.context["meta"]["strategy"], "best_card_fallback")

    def test_excluded_deals_are_listed_with_their_minimum(self):
        response = self.get(brand="BigBasket", amount=100)
        self.assertContains(response, "Not usable at this amount")
        self.assertEqual(len(response.context["result"]["excluded"]), 3)

    def test_invalid_input_renders_errors_not_a_500(self):
        response = self.get(brand="", amount="-5")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "field-error")
        self.assertIn("amount", response.context["errors"])
        self.assertNotIn("result", response.context)

    def test_invalid_input_is_not_recorded_as_a_search(self):
        self.get(brand="Zomato", amount="banana")
        self.assertEqual(SearchRecord.objects.count(), 0)

    def test_recent_searches_are_shown(self):
        self.get(brand="Netflix", amount=500)
        response = self.get()
        self.assertContains(response, "Recent searches")
        self.assertContains(response, "Netflix")

    def test_stylesheet_is_served(self):
        response = self.client.get("/static/deals/app.css")
        self.assertEqual(response.status_code, 200)


class BootstrapTests(SimpleTestCase):
    """The cold-start helper must refuse to touch a shared database."""

    databases = {"default"}

    @override_settings(EPHEMERAL_DATABASE=False)
    def test_no_self_migration_when_the_database_is_durable(self):
        from deals import bootstrap

        bootstrap.reset_flag()
        # Auto-migrating a shared Postgres from a request path would let two
        # cold-starting instances race the same DDL.
        self.assertFalse(bootstrap.ensure_database())

    @override_settings(EPHEMERAL_DATABASE=True)
    def test_seeded_database_needs_no_bootstrap(self):
        from deals import bootstrap

        bootstrap.reset_flag()
        # The test database is already migrated, so there is nothing to do.
        self.assertFalse(bootstrap.ensure_database())

    def test_runs_only_once_per_instance(self):
        from deals import bootstrap

        bootstrap.reset_flag()
        bootstrap.ensure_database()
        self.assertFalse(bootstrap.ensure_database())
