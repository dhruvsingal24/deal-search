"""Request validation.

Serializers here are strict: unknown fields are rejected rather than ignored,
so a typo like {"brnad": "Zomato"} fails loudly instead of silently searching
for nothing. DRF ignores extra keys by default, hence `StrictSerializer`.
"""

from decimal import Decimal

from django.conf import settings
from rest_framework import serializers

from .models import Source

MAX_AMOUNT = Decimal(settings.DEALS_MAX_AMOUNT)


class StrictSerializer(serializers.Serializer):
    """Rejects unknown fields, and reports them *alongside* field errors rather
    than short-circuiting — one round trip should surface every problem."""

    def to_internal_value(self, data):
        unknown = {}
        if isinstance(data, dict):
            unknown = {f: ["unexpected field"] for f in sorted(set(data) - set(self.fields))}
        try:
            validated = super().to_internal_value(data)
        except serializers.ValidationError as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"body": exc.detail}
            raise serializers.ValidationError({**detail, **unknown}) from exc
        if unknown:
            raise serializers.ValidationError(unknown)
        return validated


class NonBlankCharField(serializers.CharField):
    def __init__(self, **kwargs):
        kwargs.setdefault("trim_whitespace", True)
        kwargs.setdefault("allow_blank", False)
        super().__init__(**kwargs)


class SearchRequestSerializer(StrictSerializer):
    brand = NonBlankCharField(min_length=1, max_length=80)
    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        max_value=MAX_AMOUNT,
    )


class CreateDealSerializer(StrictSerializer):
    brand = NonBlankCharField(min_length=1, max_length=80)
    title = NonBlankCharField(min_length=1, max_length=200)
    source = serializers.ChoiceField(choices=Source.choices)
    discountPct = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=Decimal("0"), max_value=Decimal("100")
    )
    maxDiscount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        max_value=MAX_AMOUNT,
        required=False,
        allow_null=True,
        default=None,
    )
    minSpend = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        max_value=MAX_AMOUNT,
        required=False,
        default=Decimal("0"),
    )
    category = NonBlankCharField(max_length=50, required=False, default="general")


class HistoryQuerySerializer(StrictSerializer):
    limit = serializers.IntegerField(
        required=False,
        default=settings.DEALS_PAGE_SIZE,
        min_value=1,
        max_value=settings.DEALS_MAX_PAGE_SIZE,
    )
    cursor = serializers.CharField(required=False, allow_blank=True, default="")
    brand = serializers.CharField(required=False, allow_blank=True, max_length=80, default="")
