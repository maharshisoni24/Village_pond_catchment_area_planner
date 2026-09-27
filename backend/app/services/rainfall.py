"""
Historical rainfall lookup via Open-Meteo API.

Uses the Open-Meteo Archive API (free, no key) to fetch daily precipitation
for a given coordinate over the last N years.  Returns annual and monsoon-season
(June–September) totals.
"""

from __future__ import annotations

import logging
from datetime import date

import httpx

logger = logging.getLogger(__name__)

# Open-Meteo Archive API — free, no auth needed
_BASE_URL = "https://archive-api.open-meteo.com/v1/archive"


async def fetch_rainfall(
    lat: float,
    lon: float,
    years: int = 5,
) -> dict:
    """
    Fetch historical daily precipitation and return annual + monsoon summaries.

    Returns
    -------
    dict with keys:
        annual_avg_mm     – average total annual rainfall (mm)
        monsoon_avg_mm    – average June–Sep rainfall (mm)
        years             – number of years analysed
        yearly            – list of {year, annual_mm, monsoon_mm}
    """
    today = date.today()
    start = date(today.year - years, 1, 1)
    end = date(today.year - 1, 12, 31)  # full calendar years only

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "precipitation_sum",
        "timezone": "auto",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(_BASE_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    dates = data["daily"]["time"]           # ["2020-01-01", ...]
    precip = data["daily"]["precipitation_sum"]  # [0.0, 1.2, ...]

    # Aggregate per year
    yearly: dict[int, dict] = {}
    for d, p in zip(dates, precip):
        if p is None:
            continue
        y = int(d[:4])
        m = int(d[5:7])
        if y not in yearly:
            yearly[y] = {"annual": 0.0, "monsoon": 0.0}
        yearly[y]["annual"] += p
        if 6 <= m <= 9:
            yearly[y]["monsoon"] += p

    if not yearly:
        raise ValueError("No rainfall data returned from Open-Meteo for these coordinates")

    result_years = sorted(yearly.keys())
    yearly_list = [
        {"year": y, "annual_mm": round(yearly[y]["annual"], 1),
         "monsoon_mm": round(yearly[y]["monsoon"], 1)}
        for y in result_years
    ]
    n = len(yearly_list)

    return {
        "annual_avg_mm": round(sum(r["annual_mm"] for r in yearly_list) / n, 1),
        "monsoon_avg_mm": round(sum(r["monsoon_mm"] for r in yearly_list) / n, 1),
        "years": n,
        "yearly": yearly_list,
    }
