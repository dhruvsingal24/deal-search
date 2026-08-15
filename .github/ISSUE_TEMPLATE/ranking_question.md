---
name: Ranking looks wrong
about: A deal ranked in an order that seems incorrect
labels: ranking
---

Ranking is by **effective price** (amount − discount − reward earned on the
remainder), not headline discount. A smaller percentage often wins: see the
Amazon example in the README. Before filing, check the `effectivePrice`,
`maxDiscount` and `payWith.rewardCapped` fields on the deals involved.

**Brand and amount**

**Ranking returned** (paste the `results` array, or just id / discountPct / effectivePrice per row)

**Ranking you expected, and the arithmetic behind it**
