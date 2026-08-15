"""Latency benchmark.

Measures end-to-end HTTP latency against a running server, so the numbers
include serialisation, the ORM round trip and the history write. Run it
against gunicorn, not `runserver` — the dev server is single-threaded and
adds overhead that says nothing about the application.

    gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 2
    python bench.py --url http://127.0.0.1:8000 --n 300
"""

import argparse
import json
import statistics
import time

import httpx


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(int(round(p / 100 * (len(ordered) - 1))), len(ordered) - 1)
    return ordered[idx]


def run_case(client: httpx.Client, name: str, requests: list[tuple[str, dict]]) -> dict:
    latencies = []
    for method, payload in requests:
        started = time.perf_counter()
        if method == "search":
            response = client.post("/deals/search", json=payload)
        else:
            response = client.get("/searches", params=payload)
        response.raise_for_status()
        latencies.append((time.perf_counter() - started) * 1000)
    return {
        "case": name,
        "requests": len(latencies),
        "mean_ms": round(statistics.fmean(latencies), 3),
        "p50_ms": round(percentile(latencies, 50), 3),
        "p95_ms": round(percentile(latencies, 95), 3),
        "p99_ms": round(percentile(latencies, 99), 3),
        "max_ms": round(max(latencies), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--n", type=int, default=300)
    args = parser.parse_args()

    with httpx.Client(base_url=args.url, timeout=30) as client:
        # Warm the connection pool, the interpreter and the page cache.
        for _ in range(20):
            client.post("/deals/search", json={"brand": "Zomato", "amount": 999})
        client.delete("/cache")

        results = [
            run_case(
                client,
                "search (cache miss, deals path)",
                [("search", {"brand": "Zomato", "amount": 500 + i}) for i in range(args.n)],
            ),
            run_case(
                client,
                "search (cache hit)",
                [("search", {"brand": "Zomato", "amount": 1200}) for _ in range(args.n)],
            ),
            run_case(
                client,
                "search (best-card fallback)",
                [("search", {"brand": "IndiGo", "amount": 10000 + i}) for i in range(args.n)],
            ),
            run_case(
                client,
                "history page (limit 20)",
                [("history", {"limit": 20}) for _ in range(args.n)],
            ),
        ]
        cache_stats = client.get("/stats").json()["cache"]

    print(json.dumps({"results": results, "cache": cache_stats}, indent=2))
    width = max(len(r["case"]) for r in results)
    print(f"\n{'case'.ljust(width)}   mean     p50     p95     p99")
    for r in results:
        print(
            f"{r['case'].ljust(width)}  {r['mean_ms']:6.2f}  {r['p50_ms']:6.2f}  "
            f"{r['p95_ms']:6.2f}  {r['p99_ms']:6.2f}"
        )


if __name__ == "__main__":
    main()
