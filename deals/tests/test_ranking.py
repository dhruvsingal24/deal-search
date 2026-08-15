"""Ranking maths: discount caps, reward caps, and effective-price ordering.

These use plain objects rather than model instances — the ranking engine takes
anything with the right attributes, so the maths is testable without a DB.
"""

from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from deals.ranking import apply_deal, best_card, best_card_fallback, rank_deals, reward_on


def D(v):
    return Decimal(str(v))


def make_deal(**kw):
    base = {
        "public_id": "d1",
        "brand": "Test",
        "title": "t",
        "source": "offer",
        "discount_pct": D(10),
        "max_discount": None,
        "min_spend": D(0),
        "category": "general",
        "feeds": "partner_feed",
    }
    base.update(kw)
    return SimpleNamespace(**base)


def make_card(
    id,
    name="Card",
    network="Visa",
    base_rate=D(2),
    bonus_category=None,
    bonus_rate=None,
    reward_cap=None,
    annual_fee=D(0),
):
    return SimpleNamespace(
        id=id,
        name=name,
        network=network,
        base_rate=base_rate,
        bonus_category=bonus_category,
        bonus_rate=bonus_rate,
        reward_cap=reward_cap,
        annual_fee=annual_fee,
    )


CARD_FLAT = make_card("c_flat", "Flat 2%", base_rate=D(2))
CARD_CAPPED = make_card("c_cap", "Capped 10%", base_rate=D(10), reward_cap=D(50))
CARD_BONUS = make_card(
    "c_bonus", "Grocery 5%", base_rate=D(1), bonus_category="groceries", bonus_rate=D(5)
)


class DiscountMathTests(SimpleTestCase):
    def test_percentage_discount_is_capped_by_max_discount(self):
        price, discount = apply_deal(D(1000), make_deal(discount_pct=D(60), max_discount=D(120)))
        self.assertEqual(discount, D("120.00"))
        self.assertEqual(price, D("880.00"))

    def test_discount_never_exceeds_the_amount(self):
        price, discount = apply_deal(D(100), make_deal(discount_pct=D(100)))
        self.assertEqual((price, discount), (D("0.00"), D("100.00")))

    def test_money_rounds_half_up_not_bankers(self):
        # 0.5% of 1000.5 = 5.0025 -> 5.00; a 12.5 half-cent case rounds up.
        price, discount = apply_deal(D("101"), make_deal(discount_pct=D("2.5")))
        self.assertEqual(discount, D("2.53"))  # 2.525 -> 2.53, not 2.52
        self.assertEqual(price, D("98.47"))
        self.assertEqual(price + discount, D("101.00"))  # must reconcile


class CardSelectionTests(SimpleTestCase):
    def test_reward_cap_limits_earnings(self):
        reward, rate = reward_on(CARD_CAPPED, D(10000), "general")
        self.assertEqual(rate, D(10))
        self.assertEqual(reward, D("50.00"))  # capped, not 1000

    def test_bonus_rate_applies_only_to_its_category(self):
        self.assertEqual(reward_on(CARD_BONUS, D(1000), "groceries"), (D("50.00"), D(5)))
        self.assertEqual(reward_on(CARD_BONUS, D(1000), "travel"), (D("10.00"), D(1)))

    def test_best_card_prefers_highest_reward_then_lowest_fee(self):
        cheap = make_card("cheap", base_rate=D(3), annual_fee=D(0))
        pricey = make_card("pricey", base_rate=D(3), annual_fee=D(5000))
        card, reward, _ = best_card(D(1000), "general", [pricey, cheap])
        self.assertEqual(card.id, "cheap")
        self.assertEqual(reward, D("30.00"))

    def test_no_cards_means_no_pick(self):
        self.assertIsNone(best_card(D(1000), "general", []))

    def test_best_card_depends_on_the_amount_not_just_the_rate(self):
        """Caps make "best card" a property of (card, category, amount).

        A 10% card capped at 300 beats a 3% uncapped one until the cap binds,
        then loses. Any cache keyed on category alone would get this wrong.
        Mirrors the crossover table in docs/card-selection.md.
        """
        capped = make_card(
            "capped",
            base_rate=D(0),
            bonus_category="streaming",
            bonus_rate=D(10),
            reward_cap=D(300),
            annual_fee=D(0),
        )
        uncapped = make_card("uncapped", base_rate=D(3), annual_fee=D(12500))
        cards = [capped, uncapped]

        self.assertEqual(best_card(D(3000), "streaming", cards)[0].id, "capped")
        self.assertEqual(best_card(D(20000), "streaming", cards)[0].id, "uncapped")

        # At 10,000 both earn exactly 300, so the tie breaks on annual fee.
        card, reward, _ = best_card(D(10000), "streaming", cards)
        self.assertEqual(reward, D("300.00"))
        self.assertEqual(card.id, "capped")


class RankingTests(SimpleTestCase):
    def test_highest_headline_discount_can_lose_on_effective_price(self):
        """A 15% deal in a low-reward category loses to a 10% deal in a
        high-reward one. This is the whole reason ranking happens after
        rewards rather than on discountPct."""
        deals = [
            make_deal(public_id="hi_pct", title="15% off", discount_pct=D(15), category="general"),
            make_deal(
                public_id="lo_pct", title="10% off", discount_pct=D(10), category="groceries"
            ),
        ]
        cards = [make_card("c", base_rate=D(0), bonus_category="groceries", bonus_rate=D(40))]
        ranked, _ = rank_deals(D(1000), deals, cards)
        self.assertEqual([r["id"] for r in ranked], ["lo_pct", "hi_pct"])
        self.assertLess(ranked[0]["discountPct"], ranked[1]["discountPct"])
        self.assertLess(ranked[0]["effectivePrice"], ranked[1]["effectivePrice"])

    def test_ranks_are_dense_and_ordered(self):
        deals = [
            make_deal(public_id=f"d{i}", title=f"t{i}", discount_pct=D(i)) for i in range(1, 6)
        ]
        ranked, _ = rank_deals(D(2000), deals, [CARD_FLAT])
        self.assertEqual([r["rank"] for r in ranked], [1, 2, 3, 4, 5])
        prices = [r["effectivePrice"] for r in ranked]
        self.assertEqual(prices, sorted(prices))

    def test_ties_break_toward_instant_value(self):
        """Same effective price: an offer outranks a cashback, because the
        cashback's value arrives later."""
        deals = [
            make_deal(public_id="later", title="same", source="cashback", discount_pct=D(10)),
            make_deal(public_id="now", title="same", source="offer", discount_pct=D(10)),
        ]
        ranked, _ = rank_deals(D(1000), deals, [CARD_FLAT])
        self.assertEqual(ranked[0]["effectivePrice"], ranked[1]["effectivePrice"])
        self.assertEqual(ranked[0]["id"], "now")

    def test_deal_below_min_spend_is_excluded_with_a_reason(self):
        deals = [
            make_deal(public_id="ok", title="usable", discount_pct=D(5)),
            make_deal(
                public_id="too_big", title="needs 5000", discount_pct=D(50), min_spend=D(5000)
            ),
        ]
        ranked, excluded = rank_deals(D(1000), deals, [CARD_FLAT])
        self.assertEqual([r["id"] for r in ranked], ["ok"])
        self.assertEqual(
            excluded,
            [
                {
                    "id": "too_big",
                    "title": "needs 5000",
                    "reason": "min_spend_not_met",
                    "minSpend": D(5000),
                }
            ],
        )

    def test_effective_price_equals_amount_minus_discount_minus_reward(self):
        ranked, _ = rank_deals(D(1000), [make_deal(discount_pct=D(20))], [CARD_FLAT])
        row = ranked[0]
        self.assertEqual(row["price"], D("800.00"))
        self.assertEqual(row["rewardValue"], D("16.00"))  # 2% of 800, not of 1000
        self.assertEqual(row["effectivePrice"], D("784.00"))
        self.assertEqual(row["totalSavings"], D("216.00"))


class FallbackTests(SimpleTestCase):
    def test_fallback_uses_the_best_card_on_the_full_amount(self):
        result = best_card_fallback(D(2000), "groceries", [CARD_FLAT, CARD_BONUS])
        self.assertEqual(result["source"], "card_reward")
        self.assertEqual(result["payWith"]["cardId"], "c_bonus")
        self.assertEqual(result["effectivePrice"], D("1900.00"))  # 5% of 2000

    def test_fallback_returns_none_without_cards(self):
        self.assertIsNone(best_card_fallback(D(1000), "general", []))
