# Card selection: the algorithm

"Which card should I pay with?" looks like one question. It is at least four,
and they have very different answers — one is a two-line scan, one is provably
solvable by greedy, one needs min-cost flow, and one is NP-hard. This document
covers what the service implements today, why that is correct, and what the
harder variants need if the scope grows.

---

## 1. The problem the service solves today

**Given** an amount `x`, a merchant category `c`, and a set of cards `C` where
each card `i` has rate `rᵢ` and per-transaction reward cap `capᵢ`,
**choose** the single card maximising reward.

Reward is a function of the amount:

```
fᵢ(x) = min(rᵢ · x, capᵢ)
```

with `rᵢ` = the bonus rate when `bonus_category == c`, otherwise the base rate.
The answer is `argmaxᵢ fᵢ(x)`.

### Implementation

`deals/ranking.py::best_card` — a linear scan.

```python
scored = [(-reward_on(card, amount, category), card.annual_fee, card.id, card) for card in cards]
scored.sort()
return scored[0]
```

**O(|C|)** to evaluate, **O(|C| log |C|)** as written because it sorts to get a
total order for tie-breaking. With six seeded cards this is a few microseconds
and is not worth optimising; the sort makes ties deterministic, which matters
more than the constant factor. See §5 for when it stops being free.

### Why the cap is the whole difficulty

Without caps, `fᵢ(x) = rᵢ · x` and the best card is the highest rate, full
stop — a constant, precomputable per category, no amount needed. Caps make the
winner **depend on the amount**:

| Amount | Cobalt Cashback (2.5%, cap 500) | Vertex Infinite (3%, cap 5000) | Winner |
|---|---|---|---|
| 1,000 | 25.00 | 30.00 | Vertex |
| 20,000 | 500.00 (capped) | 600.00 | Vertex |
| 100,000 | 500.00 (capped) | 3,000.00 | Vertex |

and with a higher-rate, tightly-capped card the crossover is real:

| Amount | Lumen Streaming (10% on streaming, cap 300) | Vertex Infinite (3%) | Winner |
|---|---|---|---|
| 2,000 | 200.00 | 60.00 | **Lumen** |
| 3,000 | 300.00 (capped) | 90.00 | **Lumen** |
| 20,000 | 300.00 (capped) | 600.00 | **Vertex** |

So "best card" is not a property of a card, or even of a card and a category.
It is a property of a card, a category **and an amount**. Any design that
caches "the best card for groceries" without the amount is wrong.

---

## 2. Why greedy per-deal selection is optimal here

The ranker evaluates each deal independently, picking that deal's best card,
then sorts by effective price. It is worth being explicit about why this is not
a heuristic:

1. **The deals are alternatives, not a basket.** You redeem exactly one. So the
   choice is `min` over independent candidates, and computing each candidate's
   own optimum then taking the minimum is exactly optimal.
2. **The card choice does not constrain the deal choice.** Any card can be used
   with any deal in this model, so there is no interaction term.
3. **The cap is per-transaction.** One search is one transaction, so nothing
   the user does now consumes a budget needed later.

Break any of the three and greedy stops being optimal. §4 covers what replaces
it.

A subtlety worth keeping: the card is evaluated against the **post-discount**
price, not the requested amount, because rewards accrue on what you actually
swipe. This changes the winner whenever a cap sits between the two figures.

---

## 3. Ordering, and why ties are broken explicitly

Both the card comparison and the deal comparison use a **total order**, not
just the primary metric:

- Cards: `(−reward, annual_fee, id)`
- Deals: `(effectivePrice, −discountValue, source_priority, title)`

Ties are common with round numbers — two cards at the same rate, two deals that
land on the same effective price. Sorting on the metric alone leaves the
outcome to Python's sort stability and therefore to database row order, which
means the same query can return different "best picks" on different machines.
Extending the key until it is total is the cheapest possible fix, and
`test_best_card_prefers_highest_reward_then_lowest_fee` and
`test_ties_break_toward_instant_value` pin both.

---

## 4. The harder variants

These are not implemented. They are what the question turns into as scope
grows, and each has a known right answer.

### 4a. Split one payment across several cards

**Given** amount `x`, split it across cards to maximise total reward, where
each card still caps out.

This is a **fractional knapsack**, and greedy is provably optimal: sort by rate
descending, and fill each card up to the spend that exhausts its cap
(`capᵢ / rᵢ`) before moving to the next.

```
sort cards by rate desc
remaining = x
for card in cards:
    spend = min(remaining, cap[card] / rate[card])
    assign(card, spend)
    remaining -= spend
    if remaining == 0: break
```

**O(|C| log |C|)**. The exchange argument is the standard one: moving a rupee
from a lower-rate card to an unexhausted higher-rate card never decreases the
total, so an optimal solution can always be transformed into the greedy one.

Worth noting this only helps when a cap actually binds — otherwise the whole
amount belongs on the single best card, and §1 already returns that.

### 4b. Caps shared across a billing period

Real caps are usually monthly, not per-transaction. Now spending on a card
today consumes a budget that a better-matched purchase might need next week,
and the greedy choice can be strictly worse than optimal.

- **Offline** (all transactions known): this is a **transportation problem** —
  cards are supplies with capped capacity, transactions are demands, and the
  reward rate is the edge weight. Solve as **min-cost max-flow**, or as an LP.
  Polynomial and exact.
- **Online** (transactions arrive one at a time, as in a real service): no
  algorithm is optimal against an adversary. The practical approach is a
  threshold policy — hold back cap on a bonus-category card unless the current
  transaction's reward exceeds the expected value of the spend it displaces —
  which needs a forecast of the user's remaining spend and is really a
  forecasting problem wearing an algorithms costume.

### 4c. Which cards should I *hold*?

Choose at most `k` cards, paying their annual fees, to maximise net rewards
over a spend profile.

This is **maximum coverage**, which is NP-hard. The objective is monotone
submodular (an extra card never hurts, and helps less the more you already
hold), so the greedy "add whichever card adds the most net value" gives the
standard **1 − 1/e ≈ 63%** approximation guarantee, and in practice runs close
to optimal on realistic card counts. For a few dozen candidate cards, an exact
ILP is also perfectly tractable.

This is also the only variant where `annual_fee` should enter the arithmetic.
The service currently discloses the fee but does not amortise it, because
amortising requires assuming annual spend, which a single request cannot
observe. §4c is where that assumption legitimately exists.

### 4d. Stacking

If a coupon can combine with a cashback offer, deals stop being alternatives
and the problem becomes selecting a maximum-value **compatible subset** — a
constraint problem over a compatibility graph. With realistic stacking rules
(usually one code + one payment offer) it degenerates to a small product of two
independent choices and stays trivial; with arbitrary rules it does not.

---

## 5. When the linear scan stops being free

Ranking costs **O(|D| · |C|)** per search — every deal for the brand against
every card. Measured at 24 deals and 6 cards, ranking is a small fraction of a
~5 ms request that is dominated by the ORM round trip and the history write.

It becomes worth attention somewhere around thousands of cards, and there are
two escalating answers:

1. **Index by category.** Only cards whose `bonus_category` matches, plus the
   best base-rate card, can ever win. Cuts `|C|` to a handful. Cheap, obvious,
   and enough almost always.

2. **Precompute the upper envelope per category.** Each `fᵢ` is a two-piece
   concave function of the amount with one breakpoint at `capᵢ / rᵢ`. The
   pointwise maximum of the family is a piecewise-linear **upper envelope**;
   the winning card is constant on each piece. Build it once per category
   (near-linear in `|C|` — the number of pieces is bounded by a
   Davenport–Schinzel sequence of order 3 over the `2|C|` segments), then
   answer "best card for amount `x`" by **binary search in O(log |C|)**.
   Rebuild only when the card set changes.

I have deliberately not built either. At six cards the scan is microseconds,
the envelope is roughly sixty lines of code with real off-by-one risk around
the breakpoints, and the correct time to add it is when a profile says the
ranking loop is hot — not before. This section exists so that decision is
recorded rather than rediscovered.

---

## Summary

| Variant | Structure | Algorithm | Complexity |
|---|---|---|---|
| One card, one transaction, per-txn caps | argmax over independent functions | linear scan (implemented) | O(\|C\|) |
| …with thousands of cards | upper envelope of piecewise-linear functions | precompute + binary search | O(log \|C\|) per query |
| Split across cards | fractional knapsack | greedy by rate, provably optimal | O(\|C\| log \|C\|) |
| Period-shared caps, known spend | transportation problem | min-cost max-flow / LP | polynomial, exact |
| Period-shared caps, online | online assignment | threshold policy, no optimal guarantee | — |
| Which cards to hold | maximum coverage, submodular | greedy (1 − 1/e), or exact ILP | NP-hard |
| Stacking deals | compatible subset selection | constraint solve; trivial under real rules | depends |
