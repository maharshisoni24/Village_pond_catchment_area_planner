#!/usr/bin/env python3
"""
Traffic generator for the Village Pond OTel demonstration.

Fires a mix of realistic, slow, and failing requests at the backend to produce
rich telemetry in Jaeger, Prometheus, and Loki.

Usage:
    python scripts/generate_traffic.py [--base-url http://localhost:7000]
                                       [--iterations 5] [--concurrency 2]

What it does:
    1. N normal analysis requests with different Indian village bboxes
    2. Several /debug/slow requests (artificial delay = 3-5 s)
    3. Several /debug/error requests (deliberate failure)
    4. Invalid bbox requests to produce 422 errors
    5. Health-check pings

After running, Jaeger will show ~(N+10) traces with nested spans, Grafana will
show request-rate / error-rate / latency spikes, and Loki will have log lines
with trace_id correlation.
"""

import argparse
import asyncio
import json
import random
import sys
import time
from dataclasses import dataclass
from typing import Optional

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)


# ─── Indian village bounding boxes (lon_min, lat_min, lon_max, lat_max) ────────
# These are real village-scale areas (~4×4 km) in states covered by project
SAMPLE_BBOXES = [
    # Chhattisgarh (project region)
    [81.85, 21.22, 81.90, 21.27],
    [81.63, 21.25, 81.68, 21.30],
    # Madhya Pradesh
    [77.40, 23.20, 77.45, 23.25],
    [78.55, 22.70, 78.60, 22.75],
    # Maharashtra (Vidarbha)
    [78.90, 20.70, 78.95, 20.75],
    # Gujarat
    [72.60, 22.30, 72.65, 22.35],
    # Rajasthan
    [75.80, 26.90, 75.85, 26.95],
    # Odisha
    [85.30, 20.50, 85.35, 20.55],
]

# Invalid bboxes that should produce 422/400 errors
BAD_BBOXES = [
    [82.0, 21.0, 81.0, 22.0],    # lon_min > lon_max
    [81.0, 23.0, 82.0, 21.0],    # lat_min > lat_max
    [0.0, 0.0, 0.0, 0.0],        # zero-size
]


@dataclass
class RequestResult:
    url: str
    status: int
    duration_s: float
    error: Optional[str] = None


async def post_json(client: httpx.AsyncClient, url: str, payload: dict) -> RequestResult:
    t0 = time.perf_counter()
    try:
        resp = await client.post(url, json=payload, timeout=120.0)
        return RequestResult(url=url, status=resp.status_code, duration_s=time.perf_counter() - t0)
    except Exception as e:
        return RequestResult(url=url, status=0, duration_s=time.perf_counter() - t0, error=str(e))


async def get_url(client: httpx.AsyncClient, url: str, params: dict = None) -> RequestResult:
    t0 = time.perf_counter()
    try:
        resp = await client.get(url, params=params, timeout=60.0)
        return RequestResult(url=url, status=resp.status_code, duration_s=time.perf_counter() - t0)
    except Exception as e:
        return RequestResult(url=url, status=0, duration_s=time.perf_counter() - t0, error=str(e))


def print_result(r: RequestResult, label: str = "") -> None:
    status_icon = "✅" if 200 <= r.status < 300 else ("⚠️ " if 400 <= r.status < 500 else "❌")
    err_str = f" [{r.error}]" if r.error else ""
    print(f"  {status_icon} [{r.status}] {label:<35} {r.duration_s:.2f}s{err_str}")


async def run_traffic(base_url: str, iterations: int, concurrency: int) -> None:
    print(f"\n{'='*60}")
    print(f"  Village Pond OTel Traffic Generator")
    print(f"  Target: {base_url}")
    print(f"  Iterations: {iterations}  Concurrency: {concurrency}")
    print(f"{'='*60}\n")

    all_results: list[RequestResult] = []

    async with httpx.AsyncClient(base_url=base_url) as client:

        # ── 1. Health check ──────────────────────────────────────────────────
        print("▶ Health check")
        r = await get_url(client, "/")
        print_result(r, "GET /")
        all_results.append(r)

        print("\n▶ Debug endpoint smoke test")
        r = await get_url(client, "/debug/health")
        print_result(r, "GET /debug/health")
        all_results.append(r)

        # ── 2. Normal analysis requests (in batches) ──────────────────────────
        print(f"\n▶ Running {iterations} analysis requests (concurrency={concurrency})")
        bboxes = random.choices(SAMPLE_BBOXES, k=iterations)
        semaphore = asyncio.Semaphore(concurrency)

        async def bounded_analysis(bbox, idx):
            async with semaphore:
                payload = {"bbox": bbox, "include_rainfall": True}
                r = await post_json(client, "/api/analysis/run", payload)
                print_result(r, f"POST /api/analysis/run [{idx+1}]")
                return r

        tasks = [bounded_analysis(b, i) for i, b in enumerate(bboxes)]
        results = await asyncio.gather(*tasks)
        all_results.extend(results)

        # ── 3. Slow demo requests ─────────────────────────────────────────────
        print("\n▶ Slow requests (demonstrating latency spans in Jaeger)")
        for delay in [3.0, 5.0, 2.0]:
            r = await get_url(client, "/debug/slow", params={"delay": delay})
            print_result(r, f"GET /debug/slow?delay={delay}")
            all_results.append(r)
            await asyncio.sleep(0.5)

        # ── 4. Error demo requests ────────────────────────────────────────────
        print("\n▶ Error requests (demonstrating failed spans in Jaeger)")
        for msg in ["DEM fetch timeout", "catchment delineation failed", "invalid bbox"]:
            r = await get_url(client, "/debug/error", params={"message": msg})
            print_result(r, f"GET /debug/error [{msg[:20]}]")
            all_results.append(r)

        # ── 5. Invalid bbox requests (422 errors) ─────────────────────────────
        print("\n▶ Invalid requests (422 / 400 errors)")
        for bad_bbox in BAD_BBOXES:
            r = await post_json(client, "/api/analysis/run", {"bbox": bad_bbox})
            print_result(r, f"POST /api/analysis/run [bad bbox]")
            all_results.append(r)

        # ── 6. Rainfall endpoint ──────────────────────────────────────────────
        print("\n▶ Rainfall endpoint")
        for lat, lon in [(21.25, 81.87), (22.72, 77.40)]:
            r = await get_url(client, "/api/rainfall", params={"lat": lat, "lon": lon, "years": 3})
            print_result(r, f"GET /api/rainfall lat={lat}")
            all_results.append(r)

    # ── Summary ───────────────────────────────────────────────────────────────
    total = len(all_results)
    ok   = sum(1 for r in all_results if 200 <= r.status < 300)
    err  = sum(1 for r in all_results if r.status == 0 or r.status >= 500)
    cli_err = sum(1 for r in all_results if 400 <= r.status < 500)
    avg_dur = sum(r.duration_s for r in all_results) / total if total else 0
    slow = max((r for r in all_results), key=lambda r: r.duration_s)

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"  Total requests : {total}")
    print(f"  2xx success    : {ok}")
    print(f"  4xx client err : {cli_err}  (expected — invalid bbox / demo)")
    print(f"  5xx / network  : {err}  (expected — /debug/error demo)")
    print(f"  Avg duration   : {avg_dur:.2f}s")
    print(f"  Slowest request: {slow.url} ({slow.duration_s:.2f}s)")
    print(f"{'='*60}")
    print(f"\n  📊 Now check:")
    print(f"     Jaeger:     http://localhost:16686")
    print(f"     Grafana:    http://localhost:3000  (dashboard: Village Pond — OTel Dashboard)")
    print(f"     Prometheus: http://localhost:9090")
    print(f"     Loki:       via Grafana Explore → Loki")
    print()


def main():
    parser = argparse.ArgumentParser(description="Village Pond OTel traffic generator")
    parser.add_argument("--base-url", default="http://localhost:7000",
                        help="Backend base URL (default: http://localhost:7000)")
    parser.add_argument("--iterations", type=int, default=5,
                        help="Number of normal analysis requests (default: 5)")
    parser.add_argument("--concurrency", type=int, default=2,
                        help="Max concurrent analysis requests (default: 2)")
    args = parser.parse_args()

    asyncio.run(run_traffic(args.base_url, args.iterations, args.concurrency))


if __name__ == "__main__":
    main()
