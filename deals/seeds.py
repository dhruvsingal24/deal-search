"""Seeded sample data.

Two independent "feeds" stand in for two upstream providers. They overlap on
purpose: `merge_feeds` collapses the duplicates before anything reaches the
database, so the store holds one row per economically distinct offer.

Card names are fictional. Reward rates are illustrative sample data, not a
description of any real credit card product.
"""

from decimal import Decimal

from django.db import transaction

from .util import brand_key

# Feeds are ordered by trust: the earlier feed wins a tie during de-duplication.
FEED_PRIORITY = ["partner_feed", "affiliate_feed"]

# --------------------------------------------------------------------------
# Feed 1 — direct brand partnerships
# brand, title, source, discount_pct, max_discount, min_spend, category
# --------------------------------------------------------------------------
# fmt: off
FEED_A = [
    ("Zomato", "Flat 20% off on orders above 500", "offer", "20.00", "150.00", "500.00", "food_delivery"),
    ("Zomato", "WELCOME60: 60% off first order", "coupon", "60.00", "120.00", "200.00", "food_delivery"),
    ("Zomato", "5% cashback via partner wallet", "cashback", "5.00", "100.00", "0.00", "food_delivery"),
    ("Swiggy", "SWIGGY30: 30% off, max 75", "coupon", "30.00", "75.00", "300.00", "food_delivery"),
    ("Swiggy", "Weekend 15% instant discount", "offer", "15.00", "200.00", "800.00", "food_delivery"),
    ("BigBasket", "10% off on grocery basket", "offer", "10.00", "250.00", "1000.00", "groceries"),
    ("BigBasket", "FRESH50: 50 off above 600", "coupon", "8.34", "50.00", "600.00", "groceries"),
    ("Blinkit", "12% off on first three orders", "offer", "12.00", "120.00", "400.00", "groceries"),
    ("Netflix", "Annual plan 16% off vs monthly", "offer", "16.00", None, "0.00", "streaming"),
    ("Spotify", "Premium Duo 25% off for 3 months", "offer", "25.00", "400.00", "0.00", "streaming"),
    ("Myntra", "MYNTRA400: 400 off above 2000", "coupon", "20.00", "400.00", "2000.00", "shopping"),
    ("Myntra", "End of season 35% off", "offer", "35.00", "1500.00", "2500.00", "shopping"),
    ("Uber", "8% cashback on rides", "cashback", "8.00", "80.00", "0.00", "ride_hailing"),
    ("Amazon", "AMZN10: 10% off electronics", "coupon", "10.00", "1500.00", "5000.00", "shopping"),
]
# fmt: on

# --------------------------------------------------------------------------
# Feed 2 — affiliate network. The first four rows are the same economic offers
# as feed 1 under different marketing copy; the rest are unique to this feed.
# --------------------------------------------------------------------------
# fmt: off
FEED_B = [
    ("Zomato", "20% OFF Zomato orders (min 500)", "offer", "20.00", "150.00", "500.00", "food_delivery"),
    ("Swiggy", "Save 30% with code SWIGGY30", "coupon", "30.00", "90.00", "300.00", "food_delivery"),
    ("BigBasket", "Grocery deal: 10% off", "offer", "10.00", "250.00", "1000.00", "groceries"),
    ("Netflix", "Netflix yearly billing saves 16%", "offer", "16.00", None, "0.00", "streaming"),
    ("Zomato", "Gold members: extra 4% cashback", "cashback", "4.00", "300.00", "1500.00", "food_delivery"),
    ("Swiggy", "3% flat cashback, no minimum", "cashback", "3.00", "60.00", "0.00", "food_delivery"),
    ("Amazon", "6% cashback on Amazon spends", "cashback", "6.00", "900.00", "0.00", "shopping"),
    ("Amazon", "GROCERY15: 15% off pantry", "coupon", "15.00", "300.00", "1200.00", "groceries"),
    ("Myntra", "4% cashback sitewide", "cashback", "4.00", "500.00", "0.00", "shopping"),
    ("Spotify", "Student plan 50% off", "offer", "50.00", "700.00", "0.00", "streaming"),
    ("Netflix", "NFLX5: 5% off gift cards", "coupon", "5.00", "250.00", "0.00", "streaming"),
    ("Uber", "UBER100: 100 off above 500", "coupon", "20.00", "100.00", "500.00", "ride_hailing"),
    ("Blinkit", "2% cashback on all orders", "cashback", "2.00", "50.00", "0.00", "groceries"),
    ("BigBasket", "18% off on first order", "offer", "18.00", "300.00", "1500.00", "groceries"),
]
# fmt: on

# --------------------------------------------------------------------------
# Cards. `bonus_rate` replaces `base_rate` when the merchant category matches
# `bonus_category`. `reward_cap` caps the reward value on one transaction.
# id, name, network, base_rate, bonus_category, bonus_rate, reward_cap, annual_fee
# --------------------------------------------------------------------------
# fmt: off
CARDS = [
    ("card_aurora", "Aurora Platinum", "Visa", "1.50", "travel", "5.00", "3000.00", "2500.00"),
    ("card_meridian", "Meridian Signature", "Mastercard", "2.00", "food_delivery", "6.00", "1500.00", "1000.00"),
    ("card_nimbus", "Nimbus Everyday", "RuPay", "1.00", "groceries", "5.00", "800.00", "0.00"),
    ("card_vertex", "Vertex Infinite", "Visa", "3.00", "shopping", "7.50", "5000.00", "12500.00"),
    ("card_cobalt", "Cobalt Cashback", "Mastercard", "2.50", None, None, "500.00", "500.00"),
    ("card_lumen", "Lumen Streaming", "Visa", "0.75", "streaming", "10.00", "300.00", "0.00"),
]
# fmt: on

# Brand catalog. Includes brands with zero deals so the fallback can still
# resolve a merchant category (e.g. IndiGo -> travel).
# fmt: off
BRANDS = [
    ("Zomato", "food_delivery"), ("Swiggy", "food_delivery"),
    ("BigBasket", "groceries"), ("Blinkit", "groceries"),
    ("Netflix", "streaming"), ("Spotify", "streaming"),
    ("Myntra", "shopping"), ("Amazon", "shopping"),
    ("Uber", "ride_hailing"),
    # no deals seeded for these — they exercise the fallback path
    ("IndiGo", "travel"), ("Air India", "travel"), ("MakeMyTrip", "travel"),
    ("Croma", "shopping"), ("DMart", "groceries"),
]
# fmt: on


def dedupe_key(brand: str, source: str, discount_pct, min_spend) -> str:
    """Two rows describing the same economics are the same deal, whatever the
    marketing copy says. Titles can't be the key: the same offer arrives as
    'Flat 20% off on orders above 500' from one feed and '20% OFF Zomato
    orders (min 500)' from the other."""
    return f"{brand_key(brand)}|{source}|{Decimal(discount_pct):.4f}|{Decimal(min_spend):.2f}"


def _record(feed: str, row: tuple) -> dict:
    brand, title, source, pct, cap, min_spend, category = row
    return {
        "brand": brand,
        "brand_key": brand_key(brand),
        "title": title,
        "source": source,
        "discount_pct": Decimal(pct),
        "max_discount": Decimal(cap) if cap is not None else None,
        "min_spend": Decimal(min_spend),
        "category": category,
        "feeds": [feed],
        "dedupe_key": dedupe_key(brand, source, pct, min_spend),
    }


def _cap_value(deal: dict) -> Decimal:
    """Uncapped beats capped; between two caps the larger one is better."""
    return Decimal("Infinity") if deal["max_discount"] is None else deal["max_discount"]


def merge_feeds(*feeds) -> list[dict]:
    """Merge feeds into one de-duplicated list.

    Conflict rule: keep the row with the better cap for the user; if the caps
    tie, keep the row from the higher-priority feed. Either way, record every
    feed that reported the deal so provenance survives the merge.
    """
    merged: dict[str, dict] = {}
    for feed_name, rows in feeds:
        for row in rows:
            rec = _record(feed_name, row)
            key = rec["dedupe_key"]
            existing = merged.get(key)
            if existing is None:
                merged[key] = rec
                continue
            existing["feeds"].append(feed_name)
            if _cap_value(rec) > _cap_value(existing):
                rec["feeds"] = existing["feeds"]
                merged[key] = rec
    out = list(merged.values())
    out.sort(key=lambda d: (d["brand_key"], d["source"], -d["discount_pct"]))
    return out


def merged_deals() -> list[dict]:
    return merge_feeds(("partner_feed", FEED_A), ("affiliate_feed", FEED_B))


@transaction.atomic
def seed_database(flush: bool = False) -> dict:
    """Populate the store. Idempotent: safe to run repeatedly."""
    from .models import Brand, Card, Deal  # local import: app registry must be ready

    if flush:
        Deal.objects.all().delete()
        Card.objects.all().delete()
        Brand.objects.all().delete()

    deals = merged_deals()
    existing = set(Deal.objects.values_list("dedupe_key", flat=True))
    Deal.objects.bulk_create(
        [
            Deal(
                brand=d["brand"],
                brand_key=d["brand_key"],
                title=d["title"],
                source=d["source"],
                discount_pct=d["discount_pct"],
                max_discount=d["max_discount"],
                min_spend=d["min_spend"],
                category=d["category"],
                feeds=",".join(d["feeds"]),
                dedupe_key=d["dedupe_key"],
            )
            for d in deals
            if d["dedupe_key"] not in existing
        ]
    )

    for cid, name, network, base, bonus_cat, bonus_rate, cap, fee in CARDS:
        Card.objects.update_or_create(
            id=cid,
            defaults={
                "name": name,
                "network": network,
                "base_rate": Decimal(base),
                "bonus_category": bonus_cat,
                "bonus_rate": Decimal(bonus_rate) if bonus_rate else None,
                "reward_cap": Decimal(cap) if cap else None,
                "annual_fee": Decimal(fee),
            },
        )

    for brand, category in BRANDS:
        Brand.objects.update_or_create(
            brand_key=brand_key(brand), defaults={"brand": brand, "category": category}
        )

    raw = len(FEED_A) + len(FEED_B)
    return {
        "raw_rows": raw,
        "deals_after_dedupe": len(deals),
        "duplicates_collapsed": raw - len(deals),
        "cards": len(CARDS),
        "brands": len(BRANDS),
    }
