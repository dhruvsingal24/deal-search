"""Smoke check.

This build ships without a unit test suite, so this script is the safety net.
It boots the real WSGI stack and asserts the behaviour that is easy to break
silently: effective-price ranking, the best-card fallback, input validation,
idempotent replay, and that the frontend and its stylesheet render.

    python smoke.py                # exercise the app
    python smoke.py --cold-start   # simulate a Vercel cold start

Exits non-zero on the first failure, so it works as a CI gate and as a
pre-deploy check.
"""

import io
import json
import os
import sys
import uuid

FAILURES = []


def check(label: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        FAILURES.append(label)


def make_client(host: str = "smoke"):
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    from config.wsgi import application

    def call(path, method="GET", query="", payload=None, headers=None):
        body = json.dumps(payload).encode() if payload is not None else b""
        env = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "SERVER_NAME": host,
            "SERVER_PORT": "80",
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(body),
            "CONTENT_LENGTH": str(len(body)),
            "CONTENT_TYPE": "application/json",
            "HTTP_HOST": host,
        }
        for key, value in (headers or {}).items():
            env[f"HTTP_{key.upper().replace('-', '_')}"] = value

        captured = {}

        def start_response(status, response_headers, exc_info=None):
            captured["status"] = status

        chunks = b"".join(application(env, start_response))
        return int(captured["status"].split()[0]), chunks

    return call


def require_migrated_database() -> None:
    """Fail with an instruction rather than a traceback from deep in the ORM."""
    from django.db import connection
    from django.db.utils import OperationalError

    try:
        tables = connection.introspection.table_names()
    except OperationalError as exc:
        print(f"cannot open the database: {exc}")
        raise SystemExit(2) from exc

    if "deals_deal" not in tables:
        print(
            "database is not set up. Run:\n"
            "    python manage.py migrate\n"
            "    python manage.py seed_deals"
        )
        raise SystemExit(2)


def run_app_checks(call) -> None:
    require_migrated_database()
    print("app:")

    status, _ = call("/health")
    check("health responds", status == 200, status)

    # Ranking must be by effective price, not headline discount: Amazon's 15%
    # pantry coupon is capped and sits in a weaker reward category, so the 10%
    # coupon has to win.
    status, body = call("/deals/search", "POST", payload={"brand": "Amazon", "amount": 8000})
    data = json.loads(body) if status == 200 else {}
    check("search responds", status == 200, status)
    check(
        "ranks by effective price, not headline discount",
        data.get("bestPick", {}).get("discountPct") == 10.0
        and data.get("results", [{}])[-1].get("discountPct") == 15.0,
        data.get("bestPick"),
    )

    status, body = call("/deals/search", "POST", payload={"brand": "IndiGo", "amount": 25000})
    data = json.loads(body)
    check(
        "best-card fallback for a brand with no deals",
        data["meta"]["strategy"] == "best_card_fallback",
        data["meta"],
    )

    # Every BigBasket deal has a minimum spend, so a small amount must fall
    # through to the fallback even though the brand does have deals.
    status, body = call("/deals/search", "POST", payload={"brand": "BigBasket", "amount": 100})
    data = json.loads(body)
    check(
        "fallback when every deal is below its minimum spend",
        data["meta"]["strategy"] == "best_card_fallback" and len(data["excluded"]) == 3,
        data["meta"],
    )

    status, _ = call("/deals/search", "POST", payload={"brand": "", "amount": -5})
    check("invalid input is rejected with 422", status == 422, status)

    status, _ = call("/deals/search", "POST", payload={"brand": "Zomato", "amount": 100, "x": 1})
    check("unknown fields are rejected", status == 422, status)

    # Replaying an idempotency key must return the original response and not
    # append a second history row.
    key = {"Idempotency-Key": f"smoke-{uuid.uuid4().hex[:12]}"}
    before = len(json.loads(call("/searches", query="limit=100")[1])["items"])
    _, first = call(
        "/deals/search", "POST", payload={"brand": "Zomato", "amount": 1200}, headers=key
    )
    for _ in range(2):
        call("/deals/search", "POST", payload={"brand": "Zomato", "amount": 1200}, headers=key)
    after = len(json.loads(call("/searches", query="limit=100")[1])["items"])
    check("3 identical keyed searches write 1 history row", after - before == 1, after - before)

    status, _ = call(
        "/deals/search", "POST", payload={"brand": "Swiggy", "amount": 900}, headers=key
    )
    check("key reuse with a different body is a 409", status == 409, status)

    page = json.loads(call("/searches", query="limit=1")[1])
    check("history paginates with a cursor", page["pageInfo"]["nextCursor"] is not None, page)

    status, _ = call("/searches", query="cursor=nonsense!!")
    check("malformed cursor is a 400", status == 400, status)

    status, body = call("/", query="brand=Zomato&amount=1200")
    check("frontend renders", status == 200 and b"Best pick" in body, status)

    status, _ = call("/static/deals/app.css")
    check("stylesheet is served", status == 200, status)


def run_cold_start_checks() -> None:
    print("serverless cold start:")
    os.environ["VERCEL"] = "1"
    os.environ.setdefault("VERCEL_URL", "smoke.vercel.app")

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from django.conf import settings

    from api.index import app  # noqa: F401  (importing runs the bootstrap)

    check("ephemeral database detected", settings.EPHEMERAL_DATABASE is True)
    check(
        "ALLOWED_HOSTS is not a wildcard",
        "*" not in settings.ALLOWED_HOSTS,
        settings.ALLOWED_HOSTS,
    )

    call = make_client(host=os.environ["VERCEL_URL"])
    status, body = call("/health")
    check("bootstrapped instance serves /health", status == 200, status)
    if status == 200:
        payload = json.loads(body)
        check("seed data present after cold start", payload["deals"] > 0, payload)


def main() -> int:
    if "--cold-start" in sys.argv:
        run_cold_start_checks()
    else:
        run_app_checks(make_client())

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
