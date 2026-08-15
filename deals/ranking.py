"""Ranking engine.

Pure functions over anything exposing the right attributes, so this module
imports no Django and the maths is unit-testable without a database. Model
methods on Card delegate here.

The whole idea: the headline discount is not what the user pays. What they
pay is

    effective price = amount - deal discount - reward earned on what's left

so every candidate is scored end to end, including the best card to pay the
remaining balance with, and the list is sorted by that number ascending.
"""

from decimal import Decimal

from .util import money

HUNDRED = Decimal("100")
ZERO = Decimal("0")

# When two candidates land on the same effective price, prefer the one whose
# value is instant over the one that arrives later.
SOURCE_PRIORITY = {"offer": 0, "coupon": 1, "cashback": 2, "card_reward": 3}


# --------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------


def rate_for(card, category) -> Decimal:
    if card.bonus_category and card.bonus_rate is not None and category == card.bonus_category:
        return Decimal(card.bonus_rate)
    return Decimal(card.base_rate)


def reward_on(card, amount, category) -> tuple[Decimal, Decimal]:
    """Returns (reward_value, applied_rate)."""
    rate = rate_for(card, category)
    reward = Decimal(amount) * rate / HUNDRED
    if card.reward_cap is not None:
        reward = min(reward, Decimal(card.reward_cap))
    return money(reward), rate


def best_card(amount, category, cards):
    """Card earning the most reward value on `amount` in `category`.

    Ties break toward the lower annual fee, then the card id, so the answer is
    deterministic across runs.
    """
    amount = Decimal(amount)
    if not cards or amount <= ZERO:
        return None
    scored = []
    for card in cards:
        reward, rate = reward_on(card, amount, category)
        scored.append((-reward, Decimal(card.annual_fee), str(card.id), card, reward, rate))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    _, _, _, card, reward, rate = scored[0]
    return card, reward, rate


def card_payload(card, reward: Decimal, rate: Decimal) -> dict:
    return {
        "cardId": card.id,
        "cardName": card.name,
        "network": card.network,
        "rewardRate": rate,
        "rewardValue": reward,
        "rewardCapped": card.reward_cap is not None and reward >= Decimal(card.reward_cap),
        "annualFee": Decimal(card.annual_fee),
    }


# --------------------------------------------------------------------------
# Deals
# --------------------------------------------------------------------------


def apply_deal(amount, deal) -> tuple[Decimal, Decimal]:
    """Returns (price_after_discount, discount_value) for this amount.

    The discount is rounded *first* and the price derived from the rounded
    figure. Rounding both independently would let `price + discount` differ
    from the amount by a cent, which is exactly the sort of thing that turns
    into a reconciliation ticket.
    """
    amount = Decimal(amount)
    discount = amount * Decimal(deal.discount_pct) / HUNDRED
    if deal.max_discount is not None:
        discount = min(discount, Decimal(deal.max_discount))
    discount = money(min(discount, amount))
    return money(amount - discount), discount


def score_deal(amount, deal, cards) -> dict:
    """Score one deal end to end for the requested amount."""
    amount = Decimal(amount)
    price, discount_value = apply_deal(amount, deal)

    pay_with, reward = None, ZERO
    # Rewards accrue on what you actually swipe, so the card is evaluated
    # against the discounted price, not the original amount.
    pick = best_card(price, deal.category, cards)
    if pick:
        card, reward, rate = pick
        pay_with = card_payload(card, reward, rate)

    effective = money(max(price - reward, ZERO))
    savings = money(amount - effective)
    return {
        "id": deal.public_id,
        "brand": deal.brand,
        "title": deal.title,
        "source": deal.source,
        "discountPct": Decimal(deal.discount_pct),
        "price": price,  # what you pay at checkout
        "listPrice": money(amount),
        "discountValue": discount_value,
        "payWith": pay_with,
        "rewardValue": reward,
        "effectivePrice": effective,  # what it really costs you
        "totalSavings": savings,
        "savingsPct": money(savings / amount * HUNDRED) if amount else ZERO,
        "minSpend": Decimal(deal.min_spend),
        "maxDiscount": Decimal(deal.max_discount) if deal.max_discount is not None else None,
        "category": deal.category,
        "feeds": [f for f in (deal.feeds or "").split(",") if f],
    }


def rank_deals(amount, deals, cards) -> tuple[list[dict], list[dict]]:
    """Returns (ranked_results, excluded).

    A deal whose minimum spend is above the requested amount can't be used, so
    it is reported separately rather than silently dropped — "you're 200 short
    of a better deal" is useful information.
    """
    amount = Decimal(amount)
    scored, excluded = [], []
    for deal in deals:
        if amount < Decimal(deal.min_spend):
            excluded.append(
                {
                    "id": deal.public_id,
                    "title": deal.title,
                    "reason": "min_spend_not_met",
                    "minSpend": Decimal(deal.min_spend),
                }
            )
            continue
        scored.append(score_deal(amount, deal, cards))

    scored.sort(
        key=lambda r: (
            r["effectivePrice"],
            -r["discountValue"],
            SOURCE_PRIORITY.get(r["source"], 99),
            r["title"],
        )
    )
    for i, row in enumerate(scored, start=1):
        row["rank"] = i
    return scored, excluded


def best_card_fallback(amount, category, cards) -> dict | None:
    """No usable deal: the cheapest way to pay is simply the card that earns
    the most on the full amount. Returned in the same shape as a deal, so
    clients get one uniform `bestPick` contract on both paths."""
    amount = Decimal(amount)
    pick = best_card(amount, category, cards)
    if not pick:
        return None
    card, reward, rate = pick
    effective = money(max(amount - reward, ZERO))
    return {
        "id": card.id,
        "brand": None,
        "title": f"Pay with {card.name} — earn {rate.normalize():f}% back",
        "source": "card_reward",
        "discountPct": ZERO,
        "price": money(amount),
        "listPrice": money(amount),
        "discountValue": ZERO,
        "payWith": card_payload(card, reward, rate),
        "rewardValue": reward,
        "effectivePrice": effective,
        "totalSavings": reward,
        "savingsPct": money(reward / amount * HUNDRED) if amount else ZERO,
        "minSpend": ZERO,
        "maxDiscount": Decimal(card.reward_cap) if card.reward_cap is not None else None,
        "category": category,
        "feeds": ["cards"],
        "rank": 1,
    }
