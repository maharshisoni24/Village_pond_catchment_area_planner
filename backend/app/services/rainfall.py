"""
Historical rainfall lookup via Open-Meteo API with regional fallback.

Primary source: Open-Meteo Archive API (free, no key).
Fallback:       Hardcoded regional averages for India when the API is
                unreachable (DNS blocked on restricted networks, 429
                rate-limit, or timeout).  Accuracy ≈ ±15% — good enough
                for pond sizing order-of-magnitude estimates.

Why regional fallback?
  SSH/lab machines often block external DNS for non-whitelisted domains.
  Returning None breaks the water-volume requirement; an estimate is far
  more useful and still scientifically reasonable for rural planning.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://archive-api.open-meteo.com/v1/archive"

# ─── Regional rainfall averages for India (annual mm, monsoon mm) ─────────────
# Source: IMD climatological normals (1981-2010), rough 2°×2° grid.
# Format: (lat_min, lat_max, lon_min, lon_max): (annual_mm, monsoon_mm)
_INDIA_RAINFALL_ZONES: list[tuple] = [
    # Chhattisgarh / eastern MP (project region)
    (19, 25, 79, 85, 1250, 1050),
    # Western MP / Rajasthan border
    (22, 27, 74, 80, 850, 720),
    # Rajasthan (arid)
    (24, 30, 70, 76, 350, 300),
    # Gangetic plain (UP / Bihar)
    (24, 28, 77, 88, 1000, 850),
    # Maharashtra (Vidarbha)
    (18, 23, 76, 81, 950, 820),
    # Odisha / Jharkhand
    (19, 24, 83, 88, 1450, 1250),
    # Kerala / coastal Karnataka
    (8, 15, 74, 78, 2800, 1800),
    # Tamil Nadu
    (8, 14, 76, 80, 950, 380),
    # Northeast India
    (22, 28, 88, 97, 2500, 2000),
    # Gujarat
    (20, 25, 68, 75, 600, 520),
    # Punjab / Haryana
    (28, 32, 73, 78, 550, 460),
]
_DEFAULT_ANNUAL_MM  = 1100.0   # pan-India average fallback
_DEFAULT_MONSOON_MM =  880.0


def _regional_estimate(lat: float, lon: float) -> dict:
    """Return IMD-based regional rainfall estimate when the API is unreachable."""
    for lat_min, lat_max, lon_min, lon_max, annual, monsoon in _INDIA_RAINFALL_ZONES:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            logger.info(
                "Rainfall API unavailable — using regional estimate for "
                "(%.3f, %.3f): %.0f mm/yr", lat, lon, annual
            )
            return {
                "annual_avg_mm": float(annual),
                "monsoon_avg_mm": float(monsoon),
                "years": 0,
                "yearly": [],
                "source": "regional_estimate",   # signals UI that this is estimated
            }
    # Pan-India fallback
    logger.info("Rainfall API unavailable — using pan-India fallback: %.0f mm/yr",
                _DEFAULT_ANNUAL_MM)
    return {
        "annual_avg_mm": _DEFAULT_ANNUAL_MM,
        "monsoon_avg_mm": _DEFAULT_MONSOON_MM,
        "years": 0,
        "yearly": [],
        "source": "regional_estimate",
    }


async def fetch_rainfall(
    lat: float,
    lon: float,
    years: int = 5,
) -> dict:
    """
    Fetch historical daily precipitation and return annual + monsoon summaries.

    Retries once on 429 (rate-limit) after a short wait.
    Falls back to regional estimate on DNS failure, timeout, or repeated 429.

    Returns
    -------
    dict with keys:
        annual_avg_mm   – average total annual rainfall (mm)
        monsoon_avg_mm  – average June–Sep rainfall (mm)
        years           – number of years analysed (0 = regional estimate)
        yearly          – list of {year, annual_mm, monsoon_mm}
        source          – "open_meteo" or "regional_estimate"
    """
    today = date.today()
    start = date(today.year - years, 1, 1)
    end   = date(today.year - 1, 12, 31)

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "precipitation_sum",
        "timezone": "auto",
    }

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=20, http2=False) as client:
                resp = await client.get(_BASE_URL, params=params)

            if resp.status_code == 429:
                if attempt == 0:
                    logger.warning("Open-Meteo 429 rate-limit — retrying in 5s")
                    await asyncio.sleep(5)
                    continue
                # Second attempt also 429 → fall back
                logger.warning("Open-Meteo 429 persists — using regional estimate")
                return _regional_estimate(lat, lon)

            resp.raise_for_status()
            data = resp.json()
            break   # success

        except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as exc:
            # DNS failure, TCP refused, or network unreachable
            logger.warning("Open-Meteo unreachable (%s) — using regional estimate", exc)
            return _regional_estimate(lat, lon)

        except httpx.HTTPStatusError:
            raise   # other 4xx/5xx — let caller handle
    else:
        return _regional_estimate(lat, lon)

    # ── Parse response ────────────────────────────────────────────────────────
    dates  = data["daily"]["time"]
    precip = data["daily"]["precipitation_sum"]

    yearly: dict[int, dict] = {}
    for d, p in zip(dates, precip):
        if p is None:
            continue
        y, m = int(d[:4]), int(d[5:7])
        if y not in yearly:
            yearly[y] = {"annual": 0.0, "monsoon": 0.0}
        yearly[y]["annual"] += p
        if 6 <= m <= 9:
            yearly[y]["monsoon"] += p

    if not yearly:
        logger.warning("Open-Meteo returned empty data — using regional estimate")
        return _regional_estimate(lat, lon)

    yearly_list = [
        {"year": y, "annual_mm": round(yearly[y]["annual"], 1),
         "monsoon_mm": round(yearly[y]["monsoon"], 1)}
        for y in sorted(yearly)
    ]
    n = len(yearly_list)
    return {
        "annual_avg_mm":  round(sum(r["annual_mm"]  for r in yearly_list) / n, 1),
        "monsoon_avg_mm": round(sum(r["monsoon_mm"] for r in yearly_list) / n, 1),
        "years": n,
        "yearly": yearly_list,
        "source": "open_meteo",
    }
