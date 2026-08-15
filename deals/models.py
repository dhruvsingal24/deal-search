"""Data model.

Money is `DecimalField` throughout — floats accumulate error and this service
adds and subtracts currency on every request.
"""

import uuid

from django.db import models

from . import ranking
from .util import brand_key


def deal_id() -> str:
    return f"deal_{uuid.uuid4().hex[:12]}"


def search_id() -> str:
    return f"srch_{uuid.uuid4().hex[:12]}"


class Source(models.TextChoices):
    OFFER = "offer", "Offer"
    COUPON = "coupon", "Coupon"
    CASHBACK = "cashback", "Cashback"
    CARD_REWARD = "card_reward", "Card reward"


class Brand(models.Model):
    """brand_key -> display name + merchant category.

    Exists so the best-card fallback still works for brands with *zero* deals:
    IndiGo has no offers seeded, but it is catalogued as `travel`, so the
    fallback picks the travel-bonus card instead of defaulting to a base rate.
    """

    brand_key = models.CharField(primary_key=True, max_length=80)
    brand = models.CharField(max_length=80)
    category = models.CharField(max_length=50, default="general")

    class Meta:
        ordering = ["brand"]

    def __str__(self) -> str:
        return self.brand


class Card(models.Model):
    id = models.CharField(primary_key=True, max_length=40)
    name = models.CharField(max_length=80)
    network = models.CharField(max_length=40)
    # Percentages, e.g. 2.50 = 2.5% back.
    base_rate = models.DecimalField(max_digits=5, decimal_places=2)
    bonus_category = models.CharField(max_length=50, null=True, blank=True)
    bonus_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    # Cap on reward *value* earned in a single transaction.
    reward_cap = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    annual_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return self.name

    def rate_for(self, category):
        return ranking.rate_for(self, category)

    def reward_on(self, amount, category):
        return ranking.reward_on(self, amount, category)


class DealQuerySet(models.QuerySet):
    def for_brand(self, brand: str):
        return self.filter(brand_key=brand_key(brand))


class Deal(models.Model):
    # Numeric auto pk doubles as the keyset pagination key; `public_id` is the
    # stable identifier clients see.
    public_id = models.CharField(max_length=40, unique=True, default=deal_id, editable=False)
    brand = models.CharField(max_length=80)
    # Normalised lookup key; `brand` is display text only.
    brand_key = models.CharField(max_length=80, db_index=True)
    # Marketing copy — never used for matching, since two feeds describe the
    # same offer differently.
    title = models.CharField(max_length=200)
    source = models.CharField(max_length=20, choices=Source.choices)
    discount_pct = models.DecimalField(max_digits=5, decimal_places=2)
    # NULL means uncapped. This is what makes "60% off" worth less than
    # "20% off" once the amount is large enough.
    max_discount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    min_spend = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    category = models.CharField(max_length=50, default="general")
    # Provenance: which upstream feeds reported this deal.
    feeds = models.CharField(max_length=120, default="")
    # brand_key|source|discount_pct|min_spend — see seeds.merge_feeds().
    dedupe_key = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = DealQuerySet.as_manager()

    class Meta:
        ordering = ["brand_key", "source", "-discount_pct"]
        indexes = [models.Index(fields=["brand_key", "min_spend"])]

    def __str__(self) -> str:
        return f"{self.brand}: {self.title}"

    def save(self, *args, **kwargs):
        self.brand_key = brand_key(self.brand)
        super().save(*args, **kwargs)

    @property
    def feed_list(self) -> list[str]:
        return [f for f in (self.feeds or "").split(",") if f]


class SearchRecord(models.Model):
    """Append-only history. The auto pk doubles as the pagination key."""

    public_id = models.CharField(max_length=40, unique=True, default=search_id, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    brand = models.CharField(max_length=80)
    brand_key = models.CharField(max_length=80, db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    strategy = models.CharField(max_length=32)
    result_count = models.IntegerField(default=0)
    # Outcome is stored so history is useful for analytics, not just audit.
    best_pick_title = models.CharField(max_length=200, null=True, blank=True)
    best_pick_effective_price = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    cache_hit = models.BooleanField(default=False)
    latency_ms = models.FloatField(default=0)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"{self.brand} @ {self.amount}"


class IdempotencyRecord(models.Model):
    """Stores the exact response body issued for an Idempotency-Key."""

    key = models.CharField(max_length=200)
    endpoint = models.CharField(max_length=64)
    request_hash = models.CharField(max_length=64)
    status_code = models.IntegerField()
    response_body = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["key", "endpoint"], name="uniq_key_per_endpoint")
        ]
        indexes = [models.Index(fields=["created_at"])]

    def __str__(self) -> str:
        return f"{self.endpoint} [{self.key}]"
