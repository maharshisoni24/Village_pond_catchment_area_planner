"""
Open land detection using satellite imagery + OpenCV.

Supports two satellite providers, switchable via config.py:
  SATELLITE_PROVIDER = "mapbox"  → Mapbox Static Images API (satellite-v9)
  SATELLITE_PROVIDER = "esri"    → ESRI World Imagery (free, no key)

To switch: edit the one line in app/core/config.py.

Pipeline (same for both providers):
1. Fetch satellite image for the bounding box.
2. Convert to HSV colour space.
3. Mask bare soil (H 8-35) + sparse vegetation (H 25-75).
4. Morphological open+close to remove noise.
5. Find contours → convert to GeoJSON polygons.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

import cv2
import httpx
import numpy as np
from shapely.geometry import mapping, Polygon
from shapely.validation import make_valid

from app.core.config import MAPBOX_ACCESS_TOKEN, SATELLITE_PROVIDER

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Shared constants
# ------------------------------------------------------------------

_MIN_PATCH_AREA_M2 = 500.0  # filter out patches < 22×22 m

_HSV_RANGES = [
    # (h_lo, h_hi, s_lo, s_hi, v_lo, v_hi)
    (8,  35, 20, 200, 60, 220),   # bare soil (warm brown)
    (25, 75, 15, 160, 50, 200),   # dry/sparse vegetation (yellow-green)
]

_MORPH_KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

# ------------------------------------------------------------------
# Mapbox Static Images API
# ------------------------------------------------------------------

# Satellite-v9 style, bbox variant: [west,south,east,north]
_MAPBOX_STATIC_URL = (
    "https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static"
    "/[{west},{south},{east},{north}]/{size}x{size}"
)
_MAPBOX_SIZE = 1024   # px — max 1280 on free tier


async def _fetch_mapbox(
    west: float, south: float, east: float, north: float
) -> tuple[np.ndarray, float, float, float, float]:
    """Fetch one Static Images tile from Mapbox, return (bgr, w, n, e, s)."""
    if not MAPBOX_ACCESS_TOKEN:
        raise ValueError("MAPBOX_ACCESS_TOKEN not set — add it to backend/.env")

    url = _MAPBOX_STATIC_URL.format(
        west=west, south=south, east=east, north=north,
        size=_MAPBOX_SIZE,
    )
    async with httpx.AsyncClient(timeout=30, http2=False) as client:
        resp = await client.get(url, params={"access_token": MAPBOX_ACCESS_TOKEN})
        if resp.status_code != 200:
            raise ValueError(
                f"Mapbox Static Images API returned {resp.status_code}: "
                f"{resp.text[:200]}"
            )
        arr = np.frombuffer(resp.content, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Failed to decode Mapbox satellite image")
    return img, west, north, east, south


# ------------------------------------------------------------------
# ESRI World Imagery (free, no key)
# ------------------------------------------------------------------

_ESRI_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services"
    "/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
_ESRI_ZOOM = 15   # ~4 m/px at village scale


def _deg2tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    n = 2 ** zoom
    tx = int((lon + 180) / 360 * n)
    ty = int(
        (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi)
        / 2 * n
    )
    return tx, ty


def _tile2deg(tx: int, ty: int, zoom: int) -> tuple[float, float]:
    """NW corner of tile as (lat, lon)."""
    n = 2 ** zoom
    lon = tx / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / n))))
    return lat, lon


async def _fetch_esri_tile(zoom: int, tx: int, ty: int) -> np.ndarray:
    url = _ESRI_URL.format(z=zoom, y=ty, x=tx)
    async with httpx.AsyncClient(timeout=15, http2=False) as client:
        resp = await client.get(url, headers={"User-Agent": "village-pond-planner/1.0"})
        if resp.status_code != 200:
            raise ValueError(f"ESRI tile {zoom}/{ty}/{tx} returned {resp.status_code}")
        arr = np.frombuffer(resp.content, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"Failed to decode ESRI tile {zoom}/{ty}/{tx}")
        return img


async def _fetch_esri(
    west: float, south: float, east: float, north: float,
    zoom: int = _ESRI_ZOOM,
) -> tuple[np.ndarray, float, float, float, float]:
    """Stitch ESRI tiles covering bbox, return (bgr, actual_w, actual_n, actual_e, actual_s)."""
    tx_min, ty_min = _deg2tile(north, west, zoom)   # NW
    tx_max, ty_max = _deg2tile(south, east, zoom)   # SE

    n_tiles = (tx_max - tx_min + 1) * (ty_max - ty_min + 1)
    if n_tiles > 64:
        logger.warning("bbox needs %d tiles at z=%d — dropping to z=%d", n_tiles, zoom, zoom - 1)
        return await _fetch_esri(west, south, east, north, zoom - 1)

    actual_north, actual_west = _tile2deg(tx_min,     ty_min,     zoom)
    actual_south, actual_east = _tile2deg(tx_max + 1, ty_max + 1, zoom)

    tile_px = 256
    n_cols  = tx_max - tx_min + 1
    n_rows  = ty_max - ty_min + 1

    tasks = [
        _fetch_esri_tile(zoom, tx, ty)
        for ty in range(ty_min, ty_max + 1)
        for tx in range(tx_min, tx_max + 1)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    canvas = np.zeros((n_rows * tile_px, n_cols * tile_px, 3), dtype=np.uint8)
    for idx, tile in enumerate(results):
        if isinstance(tile, Exception):
            logger.warning("ESRI tile failed: %s", tile)
            continue
        row_idx = idx // n_cols
        col_idx = idx %  n_cols
        y0 = row_idx * tile_px
        x0 = col_idx * tile_px
        canvas[y0: y0 + tile_px, x0: x0 + tile_px] = tile

    return canvas, actual_west, actual_north, actual_east, actual_south


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------

async def detect_open_land(bbox: list[float]) -> dict[str, Any]:
    """
    Detect open land patches using the configured satellite provider.

    Parameters
    ----------
    bbox : [lon_min, lat_min, lon_max, lat_max]

    Returns
    -------
    dict — open_land_areas (GeoJSON FeatureCollection), patch_count,
           total_area_m2, method_notes.
    """
    west, south, east, north = bbox

    # --- Route to provider ---
    provider = SATELLITE_PROVIDER.lower()
    if provider == "mapbox":
        logger.info("Land detect: using Mapbox Static Images API")
        img_bgr, img_west, img_north, img_east, img_south = await _fetch_mapbox(
            west, south, east, north
        )
        provider_label = "Mapbox satellite-v9 (Static Images API)"
    else:
        logger.info("Land detect: using ESRI World Imagery (zoom %d)", _ESRI_ZOOM)
        img_bgr, img_west, img_north, img_east, img_south = await _fetch_esri(
            west, south, east, north
        )
        provider_label = f"ESRI World Imagery (zoom {_ESRI_ZOOM})"

    h_img, w_img = img_bgr.shape[:2]

    # --- HSV open-land mask ---
    img_hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    mask = np.zeros((h_img, w_img), dtype=np.uint8)
    for h_lo, h_hi, s_lo, s_hi, v_lo, v_hi in _HSV_RANGES:
        lo = np.array([h_lo, s_lo, v_lo], dtype=np.uint8)
        hi = np.array([h_hi, s_hi, v_hi], dtype=np.uint8)
        mask |= cv2.inRange(img_hsv, lo, hi)

    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  _MORPH_KERNEL, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _MORPH_KERNEL, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # --- Convert contours → geo-polygons ---
    lon_span   = img_east  - img_west
    lat_span   = img_north - img_south
    mid_lat    = (img_north + img_south) / 2.0
    m_per_px_x = lon_span / w_img * 111_320 * math.cos(math.radians(mid_lat))
    m_per_px_y = lat_span / h_img * 111_320
    px_area_m2 = m_per_px_x * m_per_px_y

    features: list[dict] = []
    total_area_m2 = 0.0

    for cnt in contours:
        area_m2 = cv2.contourArea(cnt) * px_area_m2
        if area_m2 < _MIN_PATCH_AREA_M2:
            continue

        pts = cnt.squeeze(axis=1) if cnt.ndim == 3 else cnt
        coords = []
        for pt in pts:
            px, py = int(pt[0]), int(pt[1])
            lon = img_west  + (px / w_img) * lon_span
            lat = img_north - (py / h_img) * lat_span
            if west <= lon <= east and south <= lat <= north:
                coords.append((lon, lat))

        if len(coords) < 3:
            continue

        coords.append(coords[0])
        try:
            poly = make_valid(Polygon(coords))
        except Exception:
            continue

        if not poly.is_valid or poly.is_empty:
            continue

        total_area_m2 += area_m2
        features.append({
            "type": "Feature",
            "geometry": mapping(poly),
            "properties": {
                "area_m2": round(area_m2, 1),
                "suitable_for_pond": True,
            },
        })

    logger.info(
        "Land detection [%s]: bbox=%s → %d patches, total %.0f m²",
        provider, bbox, len(features), total_area_m2,
    )

    return {
        "open_land_areas": {"type": "FeatureCollection", "features": features},
        "patch_count": len(features),
        "total_area_m2": round(total_area_m2, 1),
        "method_notes": (
            f"{provider_label} fetched for bounding box; "
            "HSV colour segmentation detects bare soil (H 8-35) and "
            "dry/sparse vegetation (H 25-75); "
            "morphological open+close removes noise; "
            f"minimum patch {_MIN_PATCH_AREA_M2:.0f} m²."
        ),
    }
