"""
Built-up area detector using OSM API v0.6 /map endpoint.

Fetches all OSM way data for a bounding box and extracts two categories
of exclusion zones:

  1. BUILDING / LANDUSE polygons — any way tagged building=*, landuse=residential,
     commercial, industrial, retail, garages, or amenity=school/hospital/etc.
     These are closed ways → treated as Shapely Polygons.

  2. ROAD / HIGHWAY lines — any way tagged highway=* (residential, service,
     primary, secondary, tertiary, unclassified, track …).
     These are open ways → treated as Shapely LineStrings, buffered by
     ROAD_BUFFER_DEG (~33 m each side) to produce an exclusion strip.

Why roads?  In newly planned / under-construction areas (like the IIT Bhilai
layout) OSM has no building or landuse polygons yet — only the road network.
224 of 237 ways in such areas are highways.  Without road exclusion the pond
sites up inside a road grid.

Why OSM API?  All Overpass mirrors are blocked on the IIT campus network.
The OSM API v0.6 /map endpoint uses the same data and is accessible with
http2=False (campus proxy blocks HTTP/2 ALPN negotiation).

Fallback: unreachable API or 0 features → all-False mask, pond siting
continues without exclusion.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

import httpx
import numpy as np
from shapely.geometry import LineString, Point, Polygon, mapping
from shapely.ops import unary_union
from shapely.validation import make_valid

logger = logging.getLogger(__name__)

_OSM_MAP_URL = "https://api.openstreetmap.org/api/0.6/map"

# Road buffer in degrees — ~33 m each side at Indian latitudes (lat ~21°).
# Total exclusion strip width: ~66 m → enough to span a SRTM 111 m cell.
ROAD_BUFFER_DEG: float = 0.0003

# Highway values to exclude.  "None" means exclude any value.
_ROAD_HIGHWAY_VALUES = {
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "service", "track",
    "motorway_link", "trunk_link", "primary_link", "secondary_link",
    "tertiary_link",
}

# Polygon (closed-way) tags to exclude — landuse, building, amenity.
_POLYGON_TAG_RULES = [
    ("building",  None),
    ("landuse",   "residential"),
    ("landuse",   "commercial"),
    ("landuse",   "industrial"),
    ("landuse",   "retail"),
    ("landuse",   "garages"),
    ("landuse",   "construction"),
    ("amenity",   "school"),
    ("amenity",   "hospital"),
    ("amenity",   "college"),
    ("amenity",   "university"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def detect_built_up(
    bbox: list[float],
    meta: dict,
) -> tuple[np.ndarray, list[dict]]:
    """
    Fetch OSM built-up polygons + road buffers for the bbox and rasterise
    onto the DEM grid.

    Parameters
    ----------
    bbox : [lon_min, lat_min, lon_max, lat_max]
    meta : DEM grid metadata (transform, shape, cell_size)

    Returns
    -------
    mask     : bool ndarray, True = excluded cell
    features : list of GeoJSON Feature dicts for frontend display
    """
    west, south, east, north = bbox

    span = (east - west) * (north - south)
    if span > 0.25:
        logger.warning(
            "Bbox too large for OSM API (%.3f sq deg > 0.25). "
            "Built-up exclusion skipped.", span,
        )
        return np.zeros(meta["shape"], dtype=bool), []

    geometries, geojson_features = await _fetch_and_parse(west, south, east, north)

    if not geometries:
        logger.warning("OSM API: 0 exclusion features for bbox %s", bbox)
        return np.zeros(meta["shape"], dtype=bool), []

    logger.info(
        "OSM API: %d exclusion geometries (%d GeoJSON features) for bbox %s",
        len(geometries), len(geojson_features), bbox,
    )
    mask = _rasterise(geometries, meta)
    return mask, geojson_features


# ---------------------------------------------------------------------------
# Fetch + Parse
# ---------------------------------------------------------------------------

async def _fetch_and_parse(
    west: float, south: float, east: float, north: float,
) -> tuple[list, list[dict]]:
    """Fetch OSM XML and return (shapely_geometries, geojson_features)."""
    url = f"{_OSM_MAP_URL}?bbox={west},{south},{east},{north}"
    try:
        async with httpx.AsyncClient(timeout=30, http2=False) as client:
            resp = await client.get(
                url, headers={"User-Agent": "village-pond-planner/1.0"}
            )
        if resp.status_code != 200:
            logger.warning(
                "OSM API returned %d — skipping built-up exclusion",
                resp.status_code,
            )
            return [], []
        xml_bytes = resp.content
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        logger.warning("OSM API unreachable (%s) — skipping built-up exclusion", exc)
        return [], []

    return _parse_osm_xml(xml_bytes)


def _parse_osm_xml(
    xml_bytes: bytes,
) -> tuple[list, list[dict]]:
    """
    Parse OSM XML.  Return:
      geometries     — list of Shapely geometry objects ready for rasterisation
      geojson_features — list of GeoJSON Feature dicts for frontend display
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.warning("OSM XML parse error: %s", exc)
        return [], []

    # Node index: id → (lon, lat)
    nodes: dict[int, tuple[float, float]] = {}
    for node in root.findall("node"):
        nid = int(node.attrib["id"])
        lat = node.attrib.get("lat")
        lon = node.attrib.get("lon")
        if lat and lon:
            nodes[nid] = (float(lon), float(lat))

    geometries = []
    geojson_features = []
    road_count = 0
    poly_count = 0

    for way in root.findall("way"):
        tags = {t.attrib["k"]: t.attrib["v"] for t in way.findall("tag")}
        nds = [int(nd.attrib["ref"]) for nd in way.findall("nd")]
        coords = [nodes[n] for n in nds if n in nodes]

        if len(coords) < 2:
            continue

        # --- Road / Highway ---
        hw = tags.get("highway", "")
        if hw in _ROAD_HIGHWAY_VALUES:
            try:
                line = LineString(coords)
                buffered = line.buffer(ROAD_BUFFER_DEG, cap_style=2)  # flat cap
                if buffered.is_empty:
                    continue
                geometries.append(buffered)
                geojson_features.append({
                    "type": "Feature",
                    "geometry": mapping(buffered),
                    "properties": {"highway": hw, "type": "road_buffer"},
                })
                road_count += 1
            except Exception:
                continue
            continue

        # --- Polygon features (building / landuse / amenity) ---
        if len(coords) < 3:
            continue
        if not _is_built_up_polygon(tags):
            continue

        try:
            poly = make_valid(Polygon(coords))
            if poly.is_empty:
                continue
            geometries.append(poly)
            geojson_features.append({
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": {**tags, "type": "built_up"},
            })
            poly_count += 1
        except Exception:
            continue

    logger.info(
        "OSM parse: %d road buffers, %d built-up polygons", road_count, poly_count
    )
    return geometries, geojson_features


def _is_built_up_polygon(tags: dict[str, str]) -> bool:
    for key, val in _POLYGON_TAG_RULES:
        if key in tags:
            if val is None or tags[key] == val:
                return True
    return False


# ---------------------------------------------------------------------------
# Rasterisation
# ---------------------------------------------------------------------------

def _rasterise(geometries: list, meta: dict) -> np.ndarray:
    """
    Burn Shapely geometries into a boolean DEM grid.

    Merges all geometries first (union) to avoid duplicate cell checks,
    then tests each grid cell against the union.
    """
    nrows, ncols = meta["shape"]
    cell_size = meta["cell_size"]
    x0 = meta["transform"][0]
    y0 = meta["transform"][3]
    dy = meta["transform"][5]  # negative

    # Union all geometries — fast bbox pre-filter per geometry
    try:
        union = unary_union(geometries).buffer(0)
    except Exception:
        # Fall back to iterating if union fails
        union = None

    mask = np.zeros((nrows, ncols), dtype=bool)

    if union is not None and not union.is_empty:
        minx, miny, maxx, maxy = union.bounds
        col_lo = max(0, int((minx - x0) / cell_size))
        col_hi = min(ncols, int((maxx - x0) / cell_size) + 2)
        row_lo = max(0, int((y0 - maxy) / (-dy)))
        row_hi = min(nrows, int((y0 - miny) / (-dy)) + 2)

        for r in range(row_lo, row_hi):
            for c in range(col_lo, col_hi):
                cx = x0 + (c + 0.5) * cell_size
                cy = y0 + (r + 0.5) * dy
                if union.contains(Point(cx, cy)):
                    mask[r, c] = True
    else:
        # Per-geometry fallback
        for geom in geometries:
            try:
                if geom.is_empty:
                    continue
                minx, miny, maxx, maxy = geom.bounds
                col_lo = max(0, int((minx - x0) / cell_size))
                col_hi = min(ncols, int((maxx - x0) / cell_size) + 2)
                row_lo = max(0, int((y0 - maxy) / (-dy)))
                row_hi = min(nrows, int((y0 - miny) / (-dy)) + 2)
                for r in range(row_lo, row_hi):
                    for c in range(col_lo, col_hi):
                        cx = x0 + (c + 0.5) * cell_size
                        cy = y0 + (r + 0.5) * dy
                        if geom.contains(Point(cx, cy)):
                            mask[r, c] = True
            except Exception:
                continue

    pct = 100 * mask.mean()
    logger.info(
        "Built-up raster: %d cells (%.1f%%) excluded (roads + built-up)",
        mask.sum(), pct,
    )
    return mask
