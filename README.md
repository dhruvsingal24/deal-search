# Deal Search Service (Django)

Given a brand and an amount someone wants to spend, this service returns the cheapest way to pay for it — every matching deal, ranked by what it *actually* costs after credit-card rewards, plus one best pick. When no deal is usable, it falls back to recommending the card that earns the most on that amount.

One Django project, one app, one SQLite file. No external calls, no queues, no workers: the seed data ships with the code.

---

## Quick start

```bash
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py seed_deals
python manage.py runserver 8000
```

```bash
curl -s localhost:8000/deals/search \
  -H 'Content-Type: application/json' \
  -d '{"brand":"Zomato","amount":1200}' | jq
```

Open <http://localhost:8000/> for the web frontend, or use the JSON API directly.

Verify: `python -m pytest` (unit tests) and `python smoke.py` (end-to-end). Lint: `ruff check .`

---

## The core idea

The headline discount is not what you pay. What you pay is:

```
effective price = amount − deal discount − reward earned on the remaining balance
```

Ranking on `discountPct` gets the answer wrong routinely, because discounts are capped, minimum spends disqualify offers, and reward rates vary by merchant category. A real result from the seeded data — `{"brand": "Amazon", "amount": 8000}`:

| Deal | Headline | You pay | Card reward | **Effective** | Rank |
|---|---|---|---|---|---|
| GROCERY15: 15% off pantry | **15%** | 7700.00 | 385.00 (Nimbus, groceries 5%) | **7315.00** | 3 |
| AMZN10: 10% off electronics | 10% | 7200.00 | 540.00 (Vertex, shopping 7.5%) | **6660.00** | **1** |
| 6% cashback on Amazon spends | 6% | 7520.00 | 564.00 (Vertex, shopping 7.5%) | **6956.00** | 2 |

The biggest headline number finishes last: its 300 cap bites, and its category earns a weaker reward rate. Ranking by effective price is the default sort, not a bonus mode. `test_headline_discount_does_not_decide_the_winner` pins this exact case.

---

## Layout

```
api/index.py     Vercel serverless WSGI entry point
config/          settings, urls, wsgi/asgi
deals/
  models.py      Brand, Card, Deal, SearchRecord, IdempotencyRecord
  ranking.py     pure ranking engine — imports no Django
  services.py    orchestration: ORM reads + ranking + history writes
  views.py       thin DRF views: validate, delegate, render
  views_ui.py    server-rendered HTML frontend
  bootstrap.py   cold-start migrate+seed for ephemeral deployments
  templates/     base.html, search.html
  static/        app.css
  serializers.py strict request validation
  seeds.py       two sample feeds + the merge/dedupe logic
  pagination.py  keyset cursor pagination
  idempotency.py replay/conflict handling
  cache.py       search cache + hit/miss stats
  exceptions.py  uniform error envelope
  middleware.py  X-Response-Time-Ms
  management/commands/seed_deals.py
```

`ranking.py` deliberately imports nothing from Django and operates on any object with the right attributes. Model methods delegate to it, and its tests run as `SimpleTestCase` with plain `SimpleNamespace` objects — the maths is verified without touching a database.

---

## Data model

### `Deal`

| Field | Why it exists |
|---|---|
| `id` (auto) | Numeric pk, used as the keyset pagination key. |
| `public_id` | `deal_<hex>` — the stable identifier clients see. |
| `brand`, `brand_key` | `brand` is display text; `brand_key` is the normalised lookup key (casefolded, accents and punctuation stripped), so `BigBasket`, `big basket` and `BIG-BASKET!` all hit the same row. Indexed. |
| `title` | Marketing copy. Never used for matching — two feeds describe the same offer differently. |
| `source` | `offer` \| `coupon` \| `cashback` \| `card_reward`, via `TextChoices`. |
| `discount_pct` | Percentage off. Flat-value coupons are stored as the equivalent percentage at their minimum spend, with `max_discount` holding the flat cap, so one formula covers both shapes. |
| `max_discount` | Cap on the discount value. `NULL` means uncapped. This is what makes "60% off" worth less than "20% off" at higher amounts. |
| `min_spend` | Below this the deal is unusable — reported in `excluded`, not silently dropped. |
| `category` | Merchant category. Drives which card bonus rate applies. |
| `feeds` | Provenance: which upstream feeds reported this deal. |
| `dedupe_key` | `brand_key\|source\|discount_pct\|min_spend`, `unique=True`. See [de-duplication](#two-feeds-merged-and-de-duplicated). |

### `Card`

`base_rate` applies everywhere; `bonus_rate` replaces it when `bonus_category` matches the deal's category; `reward_cap` caps the reward value on a single transaction. `annual_fee` is stored for tie-breaking and disclosure, and deliberately **not** amortised into the ranking — that would need spend-profile assumptions the service can't observe from one request.

Card names are fictional and the rates are illustrative sample data, not a description of any real product.

### `Brand`

`brand_key → (display name, category)`. This exists so the fallback still works for brands with **zero** deals: IndiGo has no offers seeded, but it's catalogued as `travel`, so the fallback picks the travel-bonus card instead of defaulting to a base rate. Brands created through `POST /deals` register here automatically.

### `SearchRecord`

Append-only history; the auto pk doubles as the pagination key. Stores the outcome (`strategy`, `result_count`, best pick, `cache_hit`, `latency_ms`) so history is useful for analytics rather than just an audit trail.

### `IdempotencyRecord`

`UniqueConstraint(key, endpoint)` with the request hash and the exact response body in a `JSONField`. In the database rather than memory so the guarantee survives a restart.

### Money is `Decimal`

Every currency and rate field is a `DecimalField`, and the ranking engine works in `Decimal` end to end. Floats accumulate error and this service adds and subtracts currency on every request. Values are converted to JSON numbers at the response boundary only, so clients don't have to parse strings.

---

## Repository

| Path | Purpose |
|---|---|
| `.github/workflows/ci.yml` | Lint and formatting, Django checks, a missing-migration check, the `deals/tests/` suite (both `manage.py test` and `pytest`, across Python 3.11–3.13), `smoke.py`, and a simulated Vercel cold start. |
| `.github/dependabot.yml` | Weekly pip updates (patches grouped into one PR), monthly Actions. |
| `.github/pull_request_template.md` | Includes a ranking-specific checklist, since that is the part where a regression is hardest to spot in review. |
| `ruff.toml` | Lint and format config. The seed data tables are `# fmt: off` — one row per line is the point of them. |
| `.env.example` | Copy to `.env`; the real file is gitignored. |
| `docs/card-selection.md` | Design note on the card-selection algorithm. |

The `test` job covers the ranking arithmetic, idempotency and pagination in
isolation; **`smoke`** sits on top of it as end-to-end cover, booting the real
WSGI stack so it catches broken settings and routing that unit tests pass
right over. **`deploy-check`** imports the serverless entry
point under Vercel's environment variables and asserts the cold start serves a
request and that `ALLOWED_HOSTS` is not `*`. `makemigrations --check` is wired
in too, because a model change committed without its migration is otherwise
found on deploy.

---

## Ranking logic

For each deal matching `brand_key`:

1. **Eligibility.** `amount < min_spend` → excluded with reason `min_spend_not_met`. It still appears in the response's `excluded` array, because "you're 200 short of a better deal" is useful information.
2. **Discount.** `discount = min(amount × pct/100, max_discount, amount)`, rounded to 2 dp **first**, then `price = amount − discount`. Rounding both independently would let `price + discount` differ from the amount by a cent.
3. **Best card for this deal.** Rewards accrue on what you actually swipe, so the card is evaluated against `price`, not `amount`, using the deal's category: `reward = min(price × rate/100, reward_cap)`.
4. **Effective price.** `effective = price − reward`.
5. **Sort** ascending on `effectivePrice`.

Tie-breaks, in order: larger discount value → source priority (`offer` → `coupon` → `cashback` → `card_reward`, preferring instant value over deferred) → title. Card selection ties break on lower annual fee, then card id. Every comparison is total, so ranking is deterministic across runs.

`bestPick` is `results[0]`; `bestCard` is the card attached to it.

Rounding is half-up via `Decimal.quantize`, never bankers' rounding — money that rounds differently from the checkout page is a support ticket.

### Choosing the card

Reward is `min(rate × amount, cap)`, and the best card is the argmax over that
— a linear scan in `best_card`. The cap is what makes it non-trivial: without
caps the answer is just the highest rate and could be precomputed per category,
but with them the winner **depends on the amount**. A 10%-on-streaming card
capped at 300 beats a 3% uncapped card up to 10,000 of spend and loses above
it, so any cache keyed on category alone would be wrong. `docs/card-selection.md`
works through the crossover, why greedy per-deal selection is provably optimal
here, and what the algorithm becomes if caps ever become monthly (min-cost
flow) or the question becomes which cards to *hold* (NP-hard, submodular,
greedy gets 1 − 1/e).

### The best-card fallback

Triggered when the ranked list is empty, which happens two ways: the brand has no deals at all, or every deal was disqualified by its minimum spend. Both are tested. The service picks the card maximising `min(amount × rate/100, cap)` for the brand's category and returns it as a synthetic `card_reward` record in `bestPick`, in the same field shape as a deal — so clients get one uniform contract on both paths. `meta.strategy` (`deals` or `best_card_fallback`) says which path served the request.

---

## API

### `POST /deals/search`

```json
{"brand": "Zomato", "amount": 1200}
```

```jsonc
{
  "query": {"brand": "Zomato", "brandKey": "zomato", "amount": 1200.0,
            "category": "food_delivery", "currency": "INR"},
  "count": 3,
  "results": [
    {
      "id": "deal_d296c6c788d1",
      "brand": "Zomato",
      "title": "Flat 20% off on orders above 500",
      "source": "offer",
      "discountPct": 20.0,
      "price": 1050.0,            // what you pay at checkout
      "listPrice": 1200.0,
      "discountValue": 150.0,     // capped at maxDiscount
      "payWith": {"cardId": "card_meridian", "cardName": "Meridian Signature",
                  "network": "Mastercard", "rewardRate": 6.0, "rewardValue": 63.0,
                  "rewardCapped": false, "annualFee": 1000.0},
      "rewardValue": 63.0,
      "effectivePrice": 987.0,    // what it really costs — the sort key
      "totalSavings": 213.0,
      "savingsPct": 17.75,
      "minSpend": 500.0, "maxDiscount": 150.0, "category": "food_delivery",
      "feeds": ["partner_feed", "affiliate_feed"],
      "rank": 1
    }
  ],
  "bestPick": { /* results[0] */ },
  "bestCard": { /* the payWith block above */ },
  "excluded": [{"id": "deal_b093afc5b07c", "title": "Gold members: extra 4% cashback",
                "reason": "min_spend_not_met", "minSpend": 1500.0}],
  "meta": {"searchId": "srch_…", "strategy": "deals", "cacheHit": false,
           "latencyMs": 1.43, "idempotencyReplayed": false}
}
```

Fallback response for `{"brand": "IndiGo", "amount": 25000}` — `results` is empty, `count` is 0, and:

```json
{
  "bestPick": {"id": "card_aurora", "title": "Pay with Aurora Platinum — earn 5% back",
               "source": "card_reward", "discountPct": 0.0, "price": 25000.0,
               "rewardValue": 1250.0, "effectivePrice": 23750.0, "rank": 1},
  "bestCard": {"cardId": "card_aurora", "rewardRate": 5.0, "rewardValue": 1250.0},
  "meta": {"strategy": "best_card_fallback"}
}
```

### `POST /deals`

Adds a deal. `201` with `created: true`, or `200` with `created: false` when an economically identical deal already exists. Invalidates the search cache.

### `GET /searches?limit=20&cursor=…&brand=…`

Cursor-paginated history, newest first. `GET /deals` takes the same parameters.

### `GET /`

The HTML frontend. Takes the same `brand` and `amount` as query parameters.

### Supporting

`GET /health` · `GET /cards` · `GET /stats` (store counts + cache stats) · `DELETE /cache`

### Errors

Every failure — validation, cursor, idempotency, 404, 405 — uses one envelope, via a DRF `EXCEPTION_HANDLER`:

```json
{"error": {"code": "validation_error", "message": "request body failed validation",
           "details": [{"field": "amount", "message": "Ensure this value is greater than or equal to 0.01.",
                        "type": "min_value"}]}}
```

Codes: `validation_error` (422), `invalid_cursor` (400), `invalid_idempotency_key` (400), `idempotency_conflict` (409), `parse_error` (400).

Validation returns **422**, not DRF's default 400: the body parsed fine, it just failed the rules. That keeps 400 meaning "malformed request".

---

## Input validation

DRF serializers, made strict — DRF ignores unknown fields by default, so `StrictSerializer` rejects them. `{"brnad": "Zomato"}` is a 422 rather than a silent search for nothing.

Unknown fields are reported *alongside* field errors rather than short-circuiting, so one round trip surfaces every problem: `{"brand":"", "amount":-5, "oops":1}` returns all three.

- `brand`: 1–80 chars, non-blank after stripping.
- `amount`: `≥ 0.01`, `≤ 10,000,000`, 2 dp.
- `discountPct`: 0–100. `source`: choice-constrained. `minSpend` / `maxDiscount`: non-negative.
- `limit`: 1–100. `Idempotency-Key`: 1–200 chars.

---

## Idempotency

Both write endpoints accept `Idempotency-Key`.

- First request under a key runs normally and the **exact response body is stored** against `(key, endpoint, request_hash)`.
- A replay with the same key and body returns the stored response with `Idempotency-Replayed: true` — same `searchId`, same deal id, and **no second history row**.
- A replay with the same key but a *different* body returns `409 idempotency_conflict` rather than a misleading success. That's a client bug, and quietly returning the old answer would hide it.
- Keys are scoped per endpoint, so the same key on `/deals` and `/deals/search` doesn't collide.
- Records are pruned past `DEALS_IDEMPOTENCY_TTL` (24h) on write.

`POST /deals/search` is idempotency-aware because it *is* a write: it appends to search history. Without a key, repeated searches are each recorded, which is the correct default for analytics.

`POST /deals` has a second line of defence: the unique `dedupe_key` means a retry without a key is a `get_or_create` no-op, not a duplicate offer.

---

## Cursor pagination

`/searches` and `/deals` use **keyset** pagination, not `LIMIT/OFFSET`. The cursor is a base64url token wrapping `{"v":1,"seq":<pk>}`; the next page is `filter(pk__lt=cursor).order_by("-pk")[:limit+1]`.

- Opaque and versioned, so the encoding can change without breaking clients.
- `limit + 1` rows are fetched to compute `hasMore` without a second `COUNT(*)`.
- `nextCursor` is `null` on the last page — the "rows are an exact multiple of the page size" case is covered by a test.
- Because it's keyset, rows inserted mid-pagination land ahead of the cursor instead of shifting the window and duplicating a row across pages. Also tested.
- Malformed or tampered cursors return `400 invalid_cursor`; an empty string means "first page", since clients that serialise a null cursor as `""` shouldn't get an error.

DRF ships a `CursorPagination` class, but it renders a `next`/`previous` URL envelope and this API returns `{items, pageInfo}`; the keyset query underneath is four lines, so it's implemented directly.

---

## Caching

Django's cache framework (`LocMemCache`, which already does TTL + LRU culling — 60s, 512 entries), wrapped with hit/miss counters so `/stats` can report them. It caches the *ranking result* only.

The important boundary: **a cache hit still writes search history.** Caching the whole request would silently drop rows from the history table, so the cache sits around the compute step, not the endpoint — `test_cache_hit_still_records_history` asserts three identical searches produce three history rows with `cacheHit` `[true, true, false]`. `meta.cacheHit` and the `X-Cache` header report which path served the request. Creating a deal flushes the cache.

Point `CACHES` at Redis for multi-replica deployment; nothing else changes.

---

## Two feeds, merged and de-duplicated

`deals/seeds.py` holds two independent feeds — `partner_feed` (14 rows) and `affiliate_feed` (14 rows) — that overlap on purpose. Merging collapses **28 raw rows into 24 stored deals**.

Titles can't be the match key, since the same offer arrives as "Flat 20% off on orders above 500" from one feed and "20% OFF Zomato orders (min 500)" from the other. The dedupe key is the economics instead: `brand_key | source | discount_pct | min_spend`. Two rows that cost the user the same thing *are* the same deal.

On conflict: keep the row with the better cap for the user (uncapped beats capped, larger cap beats smaller); if caps tie, the higher-priority feed wins. Either way both feeds are recorded in `feeds`, so provenance survives the merge. The seeded `SWIGGY30` coupon exercises this — capped at 75 in one feed and 90 in the other, the merged row keeps 90.

Known limitation: two genuinely different offers sharing brand, source, percentage *and* minimum spend would collapse into one. They'd differ only by cap, so keeping the better one is the right user-facing outcome anyway.

`python manage.py seed_deals` is idempotent (`--flush` to reset), and re-running it inserts nothing.

---

## Measured latency

gunicorn, 1 worker / 4 threads, SQLite (WAL) on local disk, 300 requests per case over HTTP via `bench.py`. Measured client-side, so the numbers include JSON serialisation, the ORM round trip and the history write. Container: Python 3.12, Django 6.1, warm cache.

| Case | mean | p50 | p95 | p99 |
|---|---|---|---|---|
| search — cache miss, deals path | 5.13 ms | 5.14 ms | 6.08 ms | 7.01 ms |
| search — cache hit | 3.32 ms | 3.23 ms | 3.90 ms | 4.63 ms |
| search — best-card fallback | 5.25 ms | 4.99 ms | 6.07 ms | 8.60 ms |
| history page (limit 20) | 2.87 ms | 2.88 ms | 3.51 ms | 3.90 ms |

The cache saves ~1.8 ms (36%). The read path is three small indexed queries, so most of the remaining cost is the history `INSERT` and serialisation, which a cache hit can't avoid by design. Per-request server-side timing is on every response as `X-Response-Time-Ms`; `meta.latencyMs` reports the search's own compute time.

Reproduce:

```bash
gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 1 --threads 4 &
python bench.py --url http://127.0.0.1:8000 --n 300
```

Benchmark against gunicorn, not `runserver` — the dev server is single-threaded and adds overhead that says nothing about the application.

---

## Verification

Two layers, run by CI and meant to be run together.

### Unit and integration tests — `deals/tests/`

```bash
python -m pytest         # 80 tests
```

- `test_ranking.py` — the arithmetic in isolation, as `SimpleTestCase` against
  plain `SimpleNamespace` objects (no database): discount capping, rounding
  half-up not bankers', the price/discount reconciliation, reward caps and
  bonus-category rates, tie-break order, and both fallback triggers.
- `test_search.py` — the API: ranking by effective price over headline
  discount, brand-key normalisation, min-spend exclusion, validation
  (including "every problem in one response"), the cache (hit/miss, cache
  key includes the amount, a write invalidates it), and the supporting
  endpoints.
- `test_idempotency.py` — replay returns the stored response and writes no
  second history row, a changed body on the same key is a 409, keys are
  scoped per endpoint, and `POST /deals`'s `dedupe_key` no-ops a keyless
  retry.
- `test_pagination.py` — the cursor walks every row exactly once, terminates
  cleanly on an exact multiple of the page size, doesn't shift when a row is
  inserted mid-walk, and the two-feed merge/dedupe conflict rules.
- `test_frontend.py` — the HTML page uses the same `cached_search` +
  `record_search` pipeline as the JSON API, plus the Vercel cold-start
  bootstrap.

### End-to-end smoke test — `smoke.py`

```bash
python smoke.py                # 13 checks against the app
python smoke.py --cold-start   # simulates a Vercel cold start
```

Boots the real WSGI stack — not the Django test client — and asserts the
behaviour that breaks silently: effective-price ranking, both best-card
fallback triggers, 422 validation, idempotent replay and 409 conflict,
cursor pagination, and that the frontend and stylesheet render. It exits
non-zero on the first failure, so it doubles as a pre-deploy check, and it's
safe to re-run against the same database.

I verified both layers catch real regressions rather than just passing:
reversing the sort to rank by headline discount instead of effective price
fails `test_headline_discount_does_not_decide_the_winner`, the equivalent
`pytest` case, and `smoke.py`.

---

## Production notes

`python manage.py check --deploy` reports four warnings, all deliberate or environment-dependent:

| Warning | Why |
|---|---|
| `W003` CSRF middleware absent | Token-less JSON API with no cookie auth; DRF's `APIView` is `csrf_exempt` regardless. Add it back alongside `SessionAuthentication` if browser sessions are introduced. |
| `W004` HSTS / `W008` SSL redirect | TLS terminates upstream in most deployments. Set `DJANGO_HSTS_SECONDS` and `DJANGO_SECURE_SSL_REDIRECT=1` when it doesn't. |
| `W009` weak `SECRET_KEY` | The default is a dev placeholder. Set `DJANGO_SECRET_KEY` in any real environment. |

`SECURE_CONTENT_TYPE_NOSNIFF`, `SECURE_REFERRER_POLICY` and `X_FRAME_OPTIONS=DENY` are on by default. Set `DJANGO_ALLOWED_HOSTS` rather than leaving the `*` default, and run with `DJANGO_DEBUG` unset.

SQLite in WAL mode handles this workload comfortably, but it serialises writes — every search appends a history row, so under sustained write concurrency this is the first thing to move to Postgres.

---

## Frontend

A server-rendered page at `/` — brand and amount in, ranked deals out, with the best pick called out and the fallback rendered differently when no deal applies.

It's deliberately a **GET form with no JavaScript**:

- A GET form carries no CSRF token, so the API stays token-less and `CsrfViewMiddleware` stays out of the stack.
- Results are linkable: `/?brand=Zomato&amount=1200` is the entire state, so a search can be shared or bookmarked.
- It works with JS disabled, and there's no client-side rendering to fall out of sync with the API.

The page calls the same `cached_search` + `record_search` path as `POST /deals/search`, so a UI search caches and records history exactly like an API one. `test_page_uses_the_same_pipeline_as_the_api` asserts both surfaces return identical rankings — the page is a second interface to one pipeline, not a fork of it.

Static files are served by WhiteNoise with `WHITENOISE_USE_FINDERS = True`, which reads straight from each app's `static/` directory. That means the CSS works with no build step at all, which matters on a platform where the build step is easy to get wrong. `collectstatic` still works for a compressed production build.

Templates live in `deals/templates/deals/` (`base.html`, `search.html`); styles in `deals/static/deals/app.css`.

---

## Deploying to Vercel

**Read this before deploying.** Vercel runs Python as serverless functions, and this service writes on *every* search — a history row, plus an idempotency record when a key is supplied. That collides with two properties of the platform:

| Constraint | Consequence here |
|---|---|
| Filesystem is ephemeral; only `/tmp` is writable | A SQLite database in `/tmp` is discarded whenever the instance recycles. Search history, added deals and idempotency records all vanish. |
| Instances are independent and short-lived | `LocMemCache` is per-instance, so cache hit rates fall and `/stats` counters reset constantly. Idempotency in `/tmp` SQLite is **not** shared between instances — the same key can execute twice on two instances. |

So: the zero-config deploy is a working demo, not a working service. The idempotency guarantee in particular is silently weakened, which is worse than it being obviously broken.

### Option A — demo deploy (no database)

```bash
npm i -g vercel
vercel deploy
```

`api/index.py` exposes the WSGI callable and `vercel.json` routes everything to it. On cold start, `deals/bootstrap.py` migrates and seeds a SQLite file in `/tmp`, so the deployment is immediately usable. The footer says plainly that the data is ephemeral.

### Option B — real deploy (recommended)

Point `DATABASE_URL` at any hosted Postgres (Vercel Postgres, Neon, Supabase). Settings pick it up through `dj-database-url` automatically and `EPHEMERAL_DATABASE` turns off — including the cold-start bootstrap, which deliberately **refuses to migrate a shared database** from a request path, since two cold-starting instances would race the same DDL.

```bash
vercel env add DATABASE_URL       # postgres://…  use the POOLED endpoint
vercel env add DJANGO_SECRET_KEY  # python -c "import secrets;print(secrets.token_urlsafe(64))"
```

Then run migrations once, at build time, by setting the project's build command to `./build_files.sh` — it installs dependencies, runs `collectstatic`, and migrates and seeds when `DATABASE_URL` is present.

Two things worth getting right:

- **Use a pooled connection string.** Every invocation opens its own connection, so `conn_max_age` is set to 0 on Vercel and pooling belongs on the database side (PgBouncer, or Neon/Supabase's pooled endpoint). An unpooled endpoint will exhaust connections under load.
- **Move the cache to Redis** (`CACHES` → `django.core.cache.backends.redis.RedisCache`) if the hit rate matters. Per-instance memory caching across ephemeral instances mostly misses.

### What actually gets deployed

`.vercelignore` keeps tests, tooling, docs, CI config and the other deploy
target out of the bundle — none of it is imported at runtime. 42 of the repo's
60 files ship; the app code is 264 KB and the rest is dependencies.

Dependencies are tiered so a serverless bundle doesn't carry a WSGI server it
will never run (Vercel invokes the WSGI callable in `api/index.py` directly):

| File | Used by | Contents |
|---|---|---|
| `requirements.txt` | Vercel | Django, DRF, WhiteNoise, dj-database-url, psycopg |
| `requirements-dev.txt` | local tooling | the above + httpx (for `bench.py`) and ruff |

Compressed bundle sizes, measured: **~12 MB** with psycopg, **~10 MB** without
it (drop that line for a SQLite-only demo). `vercel.json` sets `maxLambdaSize`
to 15 MB, so both fit with room to spare. Django itself is most of the weight —
about 31 MB uncompressed sits in `django/contrib`, nearly all of it apps this
project doesn't install.

### Environment variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres URL. Absent → SQLite (`/tmp` on Vercel). |
| `DJANGO_SECRET_KEY` | Required in production; the default is a dev placeholder. |
| `DJANGO_ALLOWED_HOSTS` | Defaults to `.vercel.app` plus `VERCEL_URL` on Vercel, `*` locally. |
| `DJANGO_DEBUG` | `1` to enable. Leave unset in production. |
| `DEALS_DB_PATH`, `DEALS_CACHE_TTL`, `DEALS_PAGE_SIZE`, `DEALS_IDEMPOTENCY_TTL`, `DEALS_CURRENCY` | Domain settings; see `config/settings.py`. |

### Honest assessment

Vercel is a reasonable host for the read path and a poor fit for the write path. Set `DATABASE_URL` to a pooled Postgres and the durability problem goes away; without it, search history and idempotency are per-instance and temporary. If those are real requirements rather than demo features, a container platform (Fly, Railway, Render) with a persistent database is a better match — this build has no `Dockerfile`, but adding one is a five-line file plus `gunicorn` in requirements.

I have not deployed this to Vercel from here — no network access to the platform — so the config follows Vercel's documented Python/WSGI conventions and is verified locally against a simulated serverless cold start (`VERCEL=1`, empty `/tmp`, requests through the WSGI callable in `api/index.py`), not against the live platform.

---

## Trade-offs

**SQLite over DynamoDB.** Zero setup, real transactions, and a `unique=True` constraint doing the de-duplication. Swapping to Postgres is a `DATABASES` change. DynamoDB would need the dedupe key as the partition key and a GSI on `brand_key`; `services.py` is the only module that would change.

**Ranking is pure and Django-free.** It's the part most likely to grow complicated, so it stays unit-testable in isolation and could move to a separate package or a worker without dragging the ORM along.

**Trimmed installed apps.** No admin, auth, sessions or templates — nothing here renders HTML or has users. That requires `REST_FRAMEWORK["UNAUTHENTICATED_USER"] = None`, since DRF otherwise resolves `AnonymousUser` from `contrib.auth`. Adding `django.contrib.admin` later is a two-line change if a back-office view is wanted.

**Rewards on the post-discount amount.** You earn on what you swipe. Cashback is treated as value now, even though it lands later — a production version would discount deferred value by an expected-realisation factor, since breakage on cashback is real.

**Annual fee is disclosed but not amortised.** Subtracting a share of it per transaction requires assuming annual spend, which one request can't tell us.

**What I'd add next:** fuzzy matching for typo'd brand names; stacking rules (many real coupons combine with cashback, which the current model treats as mutually exclusive); `valid_from`/`valid_until` on deals with filtering at query time; and Redis for the cache once there's more than one replica.
