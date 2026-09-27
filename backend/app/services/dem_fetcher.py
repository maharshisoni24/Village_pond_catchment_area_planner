"""
Fetch a DEM (Digital Elevation Model) from OpenTopoData SRTM 30m API for any
bounding box — no API key required.

Replaces the Mapbox Terrain-RGB approach (which returned zero elevations due to
tileset access restrictions) with OpenTopoData's SRTM 30m batch endpoint.

API: https://api.opentopodata.org/v1/srtm30m?locations=lat,lon|lat,lon|...
  - Free, no key, open data
  - 100 locations per request (hard limit)
  - Rate limit: 1 request/second — we batch and add a small delay between calls
  - SRTM 30m: ~30 m horizontal resolution, ±16 m vertical accuracy globally

Pipeline
--------
1. Build a regular grid of (lat, lon) points over the bbox.
2. Split into batches of 100, fetch each batch from OpenTopoData.
3. Reshape the 1-D elevation list back into a 2-D grid.
4. The grid is already north-to-south (row 0 = northernmost lat) to match
   the raster convention that pysheds and catchment.py expect.
5. Return (dem, meta) in the same format that dem_builder.build_dem() returns —
   identical keys — so catchment.py works without any changes.

Grid resolution
---------------
We build a GRID_RESOLUTION × GRID_RESOLUTION grid inside the bbox.
Default 40×40 = 1600 points → 16 API requests → ~10-16 s at campus network
speeds. Resolution is ~30m (same as SRTM native) for a typical village bbox
of 0.04°×0.04° (~4.4km × 4.4km).
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import numpy as np

logger = logging.getLogger(__name__)

# OpenTopoData SRTM 30m batch endpoint
_SRTM_URL = "https://api.opentopodata.org/v1/srtm30m"

# Grid resolution: N×N points inside the bbox.
# 40×40 = 1600 pts = 16 batches = ~12 s at 0.75 s/batch.
# Increase for larger bboxes if needed.
_GRID_RESOLUTION = 40

# OpenTopoData hard limit: 100 locations per request
_BATCH_SIZE = 100

# Delay between batches (seconds) to stay within the 1 req/s rate limit
_BATCH_DELAY_S = 0.8


async def fetch_dem_for_bbox(bbox: list[float]) -> tuple[np.ndarray, dict]:
    """
    Fetch SRTM 30m elevation data for a bounding box and return a DEM array
    plus metadata compatible with catchment.py.

    Parameters
    ----------
    bbox : [lon_min, lat_min, lon_max, lat_max]

    Returns
    -------
    dem  : 2-D float64 numpy array, shape (GRID_RESOLUTION, GRID_RESOLUTION).
           NaN where the API returns None (ocean / missing data).
    meta : dict with keys x_min, y_min, y_max, cell_size, shape, transform
           — identical format to dem_builder.build_dem() output so catchment.py
           needs zero changes.
    """
    west, south, east, north = bbox
    n = _GRID_RESOLUTION

    # Build the regular grid — row 0 = northernmost lat (matches raster convention)
    lons = np.linspace(west, east, n)
    lats = np.linspace(north, south, n)          # descending: north → south
    grid_lons, grid_lats = np.meshgrid(lons, lats)

    # Flatten to (lat, lon) pairs for the API
    flat_lats = grid_lats.ravel()
    flat_lons = grid_lons.ravel()
    points = list(zip(flat_lats, flat_lons))     # (lat, lon) for SRTM API

    logger.info(
        "Fetching SRTM elevation: bbox=%s, grid=%dx%d=%d points, %d batches",
        bbox, n, n, len(points),
        (len(points) + _BATCH_SIZE - 1) // _BATCH_SIZE,
    )

    # Fetch in sequential batches (rate-limit friendly)
    elevations: list[float | None] = []
    # http2=False: campus network proxy blocks HTTP/2 ALPN negotiation
    async with httpx.AsyncClient(timeout=30, http2=False) as client:
        for i in range(0, len(points), _BATCH_SIZE):
            batch = points[i: i + _BATCH_SIZE]
            # Retry up to 3 times on transient network errors
            for attempt in range(3):
                try:
                    batch_elevs = await _fetch_batch(client, batch)
                    break
                except (httpx.ConnectError, httpx.TimeoutException) as exc:
                    if attempt == 2:
                        raise ValueError(
                            f"OpenTopoData unreachable after 3 attempts: {exc}"
                        ) from exc
                    logger.warning(
                        "Batch %d fetch failed (attempt %d/3): %s — retrying in 2s",
                        i // _BATCH_SIZE + 1, attempt + 1, exc,
                    )
                    await asyncio.sleep(2)
            elevations.extend(batch_elevs)
            if i + _BATCH_SIZE < len(points):
                await asyncio.sleep(_BATCH_DELAY_S)

    # Reshape back to 2-D grid
    elev_arr = np.array(
        [e if e is not None else np.nan for e in elevations],
        dtype=np.float64,
    ).reshape(n, n)

    valid_pct = 100 * np.isfinite(elev_arr).mean()
    logger.info(
        "SRTM grid ready: shape=%s, elev=%.0f–%.0f m, %.0f%% valid",
        elev_arr.shape,
        float(np.nanmin(elev_arr)),
        float(np.nanmax(elev_arr)),
        valid_pct,
    )

    # Build meta — same format as dem_builder.build_dem()
    cell_lon = (east - west) / (n - 1)   # degrees per column step
    cell_lat = (north - south) / (n - 1) # degrees per row step (positive)
    # Use average cell size as the scalar 'cell_size' (catchment area calc uses it)
    cell_size = (cell_lon + cell_lat) / 2.0

    # transform: (x_origin, dx, 0, y_origin, 0, -dy)
    # x_origin = west edge of leftmost column
    # y_origin = north edge of topmost row
    # We use half-cell offsets so transform matches pixel-center convention
    x_origin = west  - cell_lon / 2
    y_origin = north + cell_lat / 2

    meta = {
        "x_min": west,
        "y_min": south,
        "y_max": north,
        "cell_size": cell_size,
        "shape": elev_arr.shape,
        "transform": (x_origin, cell_lon, 0.0, y_origin, 0.0, -cell_lat),
    }
    return elev_arr, meta


async def _fetch_batch(
    client: httpx.AsyncClient,
    points: list[tuple[float, float]],
) -> list[float | None]:
    """Fetch one batch of ≤100 (lat, lon) pairs from OpenTopoData SRTM 30m."""
    locations = "|".join(f"{lat},{lon}" for lat, lon in points)
    resp = await client.get(_SRTM_URL, params={"locations": locations})
    if resp.status_code != 200:
        raise ValueError(
            f"OpenTopoData returned {resp.status_code}: {resp.text[:200]}"
        )
    data = resp.json()
    if data.get("status") != "OK":
        raise ValueError(f"OpenTopoData error: {data.get('error', data)}")
    return [r["elevation"] for r in data["results"]]


def _make_meta(bbox: list[float], n: int = _GRID_RESOLUTION) -> dict:
    """
    Build a minimal grid meta dict for a bbox without fetching DEM data.

    Used by land.py to rasterise Overpass built-up polygons onto a grid so
    Shapely can subtract them from open land patches — without a full SRTM
    API call.
    """
    west, south, east, north = bbox
    cell_lon = (east - west) / (n - 1)
    cell_lat = (north - south) / (n - 1)
    cell_size = (cell_lon + cell_lat) / 2.0
    x_origin = west  - cell_lon / 2
    y_origin = north + cell_lat / 2
    return {
        "x_min": west,
        "y_min": south,
        "y_max": north,
        "cell_size": cell_size,
        "shape": (n, n),
        "transform": (x_origin, cell_lon, 0.0, y_origin, 0.0, -cell_lat),
    }
