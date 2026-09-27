"""
River / stream detector — two-tier approach per Architecture.md §2.1.

Tier 1 (explicit):  If the KML parser found placemarks tagged as rivers
(by name or folder), buffer those geometries and rasterise them onto the
DEM grid.  This is treated as ground truth when available.

Tier 2 (derived, always available as fallback):  After flow accumulation
is computed, classify any cell whose contributing area exceeds
RIVER_ACCUMULATION_THRESHOLD_SQM as a likely stream/river channel.

The final river mask is the union of both — explicit data wins where it
exists, derived fills the gaps.

Why two tiers?  Explicit tagging (when present) is more reliable than a
threshold on derived flow, but many contour-only KMLs lack river layers
entirely, so the derived fallback ensures the pipeline always excludes
major channels.
"""

from __future__ import annotations

import numpy as np
from shapely.geometry import Point

from app.core.config import RIVER_ACCUMULATION_THRESHOLD_SQM, RIVER_BUFFER_M
from app.services.kml_parser import RiverGeometry


def build_river_mask(
    dem_shape: tuple[int, int],
    meta: dict,
    flow_accumulation: np.ndarray,
    explicit_rivers: list[RiverGeometry],
    threshold_sqm: float = RIVER_ACCUMULATION_THRESHOLD_SQM,
    buffer_m: float = RIVER_BUFFER_M,
) -> tuple[np.ndarray, str]:
    """
    Build a boolean mask (True = river cell) over the DEM grid.

    Returns
    -------
    mask            : bool array, same shape as DEM
    detection_method: "explicit_kml_layer", "derived_flow_accumulation", or "none"
    """
    mask = np.zeros(dem_shape, dtype=bool)
    used_explicit = False

    # --- Tier 1: explicit river geometry from KML ---
    if explicit_rivers:
        _rasterise_explicit(mask, explicit_rivers, meta, buffer_m)
        used_explicit = bool(np.any(mask))

    # --- Tier 2: derived from flow accumulation ---
    derived_mask = _derived_stream_mask(flow_accumulation, meta, threshold_sqm)
    mask |= derived_mask

    if used_explicit:
        method = "explicit_kml_layer"
    elif np.any(derived_mask):
        method = "derived_flow_accumulation"
    else:
        method = "none"

    return mask, method


def nearest_river_distance_m(
    pond_lon: float,
    pond_lat: float,
    river_mask: np.ndarray,
    meta: dict,
) -> float | None:
    """
    Approximate distance (metres) from the pond point to the nearest river cell.

    Uses a simple pixel-distance calculation with a local degree-to-metre
    conversion — accurate enough for small areas, avoids heavy projections.
    """
    if not np.any(river_mask):
        return None

    cell_size = meta["cell_size"]
    x0 = meta["transform"][0]
    y0 = meta["transform"][3]
    dy = meta["transform"][5]

    river_rows, river_cols = np.where(river_mask)
    river_lons = x0 + (river_cols + 0.5) * cell_size
    river_lats = y0 + (river_rows + 0.5) * dy

    # Approximate metres per degree at the pond latitude
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * np.cos(np.radians(pond_lat))

    dx_m = (river_lons - pond_lon) * m_per_deg_lon
    dy_m = (river_lats - pond_lat) * m_per_deg_lat
    dists = np.sqrt(dx_m**2 + dy_m**2)

    return float(np.min(dists))


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _rasterise_explicit(
    mask: np.ndarray,
    rivers: list[RiverGeometry],
    meta: dict,
    buffer_m: float,
) -> None:
    """
    Burn explicit river geometries into the mask with a buffer.

    We buffer in approximate degrees (buffer_m / 111320) which is close enough
    at Indian latitudes for a 30 m buffer; avoids a full CRS transform.
    """
    cell_size = meta["cell_size"]
    x0 = meta["transform"][0]
    y0 = meta["transform"][3]
    dy = meta["transform"][5]
    nrows, ncols = mask.shape

    # ponytail: rough degree buffer — good enough for <100 m buffers
    buffer_deg = buffer_m / 111_320.0

    for river in rivers:
        buffered = river.geometry.buffer(buffer_deg)
        # Rasterise: check each cell centre against the buffered polygon
        min_col = max(0, int((buffered.bounds[0] - x0) / cell_size))
        max_col = min(ncols, int((buffered.bounds[2] - x0) / cell_size) + 1)
        min_row = max(0, int((y0 - buffered.bounds[3]) / (-dy)))
        max_row = min(nrows, int((y0 - buffered.bounds[1]) / (-dy)) + 1)

        for r in range(min_row, max_row):
            for c in range(min_col, max_col):
                cx = x0 + (c + 0.5) * cell_size
                cy = y0 + (r + 0.5) * dy
                if buffered.contains(Point(cx, cy)):
                    mask[r, c] = True


def _derived_stream_mask(
    acc: np.ndarray,
    meta: dict,
    threshold_sqm: float,
) -> np.ndarray:
    """
    Classify cells as river/stream if their contributing area exceeds the threshold.

    Contributing area = accumulation_count × cell_area_m².
    Cell area in m² is approximated from the cell size in degrees.

    Floor: always require at least MIN_CELLS upstream cells, regardless of cell
    size. Without this, coarse SRTM grids (cell ≈ 111 m, area ≈ 12 000 m²)
    hit the 50 000 m² threshold after only 4 upstream cells — classifying most
    of the grid as "river" and leaving no valid pond sites.
    """
    cell_size = meta["cell_size"]
    centre_lat = (meta["y_min"] + meta["y_max"]) / 2

    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * np.cos(np.radians(centre_lat))
    cell_area_m2 = (cell_size * m_per_deg_lat) * (cell_size * m_per_deg_lon)

    # Threshold in number of upstream cells — floor at 8 to protect coarse grids
    threshold_cells = max(8, threshold_sqm / cell_area_m2)

    return acc > threshold_cells

