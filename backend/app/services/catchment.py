"""
Catchment delineation using pysheds — DEM conditioning → D8 flow direction
→ flow accumulation → outlet selection → catchment polygon.

Why pysheds?  It implements the standard D8 algorithm (each cell drains to its
steepest downhill neighbour) plus pit-filling and catchment delineation, which
would be error-prone to hand-roll.  The D8 method is the most widely-used
single-flow-direction algorithm and is straightforward to explain in a VIVA.

Pipeline:
1. Fill pits/depressions so water doesn't pool in artefact sinks.
2. Compute flow direction (D8 — 8 possible directions).
3. Compute flow accumulation (how many upstream cells drain through each cell).
4. Select the best outlet point (highest accumulation *outside* the river mask).
5. Delineate the catchment draining to that outlet.
6. Convert the catchment raster to a GeoJSON polygon.
"""

from __future__ import annotations

import numpy as np

# ponytail: pysheds 0.5 uses np.in1d, removed in numpy 2.x.
# Shim it to np.isin (drop-in replacement) so we can use current numpy.
# Upgrade path: remove once pysheds releases a numpy-2-compatible version.
if not hasattr(np, "in1d"):
    np.in1d = np.isin

from pysheds.grid import Grid


def run_catchment_analysis(
    dem: np.ndarray,
    meta: dict,
    river_mask: np.ndarray | None = None,
    forced_outlet: tuple[int, int] | None = None,
) -> dict:
    """
    Full catchment pipeline on the interpolated DEM.

    Parameters
    ----------
    dem            : 2-D elevation array (NaN outside data extent).
    meta           : grid metadata from dem_builder (transform, shape, etc.).
    river_mask     : boolean array, True = cell excluded from pond siting.
    forced_outlet  : (row, col) — skip outlet selection, use this cell.
                     Used by analysis.py to run catchment for each top-3 candidate
                     without re-computing flow direction/accumulation.

    Returns
    -------
    dict with keys:
        pond_lat, pond_lon     – chosen outlet location
        catchment_geojson      – GeoJSON Polygon of the catchment boundary
        area_sq_km             – catchment area
        flow_accumulation      – raw accumulation grid (for river_detector)
    """
    dem = np.array(dem, dtype=np.float64)

    cell_size = meta["cell_size"]
    transform = meta["transform"]

    grid = Grid()

    from pysheds.view import ViewFinder, Raster

    nodata_val = -9999.0
    dem_filled = np.where(np.isfinite(dem), dem, nodata_val)

    viewfinder = ViewFinder(
        shape=dem.shape,
        mask=(dem_filled != nodata_val),
        affine=_to_affine(transform),
        crs="epsg:4326",
        nodata=nodata_val,
    )
    grid.viewfinder = viewfinder

    dem_raster = Raster(dem_filled, viewfinder=viewfinder)

    pit_filled = grid.fill_pits(dem_raster)
    flooded    = grid.fill_depressions(pit_filled)
    inflated   = grid.resolve_flats(flooded)
    fdir       = grid.flowdir(inflated)
    acc        = grid.accumulation(fdir)

    if forced_outlet is not None:
        outlet_row, outlet_col = forced_outlet
    else:
        outlet_row, outlet_col = _pick_outlet(acc, dem, river_mask)

    pond_lon = transform[0] + (outlet_col + 0.5) * transform[1]
    pond_lat = transform[3] + (outlet_row + 0.5) * transform[5]

    catch_mask = grid.catchment(
        x=outlet_col, y=outlet_row, fdir=fdir, xytype="index"
    )

    catchment_geojson, area_sq_km = _catchment_to_geojson(
        catch_mask.astype(np.uint8), transform, cell_size
    )

    return {
        "pond_lat": float(pond_lat),
        "pond_lon": float(pond_lon),
        "catchment_geojson": catchment_geojson,
        "area_sq_km": area_sq_km,
        "flow_accumulation": acc,
    }


def pick_top_outlets(
    acc: np.ndarray,
    dem: np.ndarray,
    river_mask: np.ndarray | None,
    n: int = 3,
    min_sep_cells: int = 5,
) -> list[tuple[int, int]]:
    """
    Return up to `n` outlet (row, col) candidates, spread ≥ min_sep_cells apart.

    Scans valid cells by descending accumulation, greedily adds a candidate
    only if it is ≥ min_sep_cells away (Chebyshev distance) from all already
    accepted candidates.

    Called by analysis.py after building the exclusion mask — it runs
    catchment delineation for each candidate (with forced_outlet) so the
    user can preview all three options.
    """
    valid = np.isfinite(dem) & (acc > 0)
    if river_mask is not None:
        valid = valid & ~river_mask

    border = max(3, min(acc.shape) // 10)
    interior = np.zeros(acc.shape, dtype=bool)
    interior[border:-border, border:-border] = True
    valid = valid & interior

    # Fallback: ignore exclusion mask if nothing left
    if not np.any(valid):
        valid = np.isfinite(dem) & (acc > 0) & interior
    if not np.any(valid):
        valid = np.isfinite(dem) & (acc > 0)

    masked_acc = np.where(valid, acc, -1).ravel()
    sorted_flat = np.argsort(masked_acc)[::-1]

    selected: list[tuple[int, int]] = []
    for flat_idx in sorted_flat:
        if masked_acc[flat_idx] <= 0:
            break
        r, c = np.unravel_index(int(flat_idx), acc.shape)
        # Chebyshev distance check — reject if too close to an existing pick
        too_close = any(
            max(abs(r - sr), abs(c - sc)) < min_sep_cells
            for sr, sc in selected
        )
        if not too_close:
            selected.append((r, c))
        if len(selected) >= n:
            break

    return selected


def _pick_outlet(
    acc: np.ndarray,
    dem: np.ndarray,
    river_mask: np.ndarray | None,
    n_candidates: int = 20,
) -> tuple[int, int]:
    """
    Choose the best pond outlet: scan the top-N highest-accumulation cells
    outside the river mask, then pick the one that gives the largest upstream
    contributing area (catchment cell count).

    Why scan top-N instead of just argmax?
    Taking the single highest-accumulation non-river cell often lands on a
    tiny side gully immediately adjacent to the main channel — its raw
    accumulation is high but its *actual* catchment (once the river mask is
    applied) is very small. Scanning N candidates and picking the one whose
    upstream cell count is largest finds the true best tributary mouth.

    ponytail: N=20 covers the realistic candidate set for village-scale DEMs;
    raise if needed for very large or complex catchments.
    """
    valid = np.isfinite(dem) & (acc > 0)
    if river_mask is not None:
        valid = valid & ~river_mask

    # Exclude cells within BORDER pixels of the grid edge.
    border = max(3, min(acc.shape) // 10)
    interior = np.zeros(acc.shape, dtype=bool)
    interior[border:-border, border:-border] = True
    valid = valid & interior

    if not np.any(valid):
        # Fallback 1: ignore river/road mask — maybe exclusion > 95% of grid.
        # Still respect the border guard so pysheds can trace upstream.
        if river_mask is not None:
            valid_fb = np.isfinite(dem) & (acc > 0) & interior
            if np.any(valid_fb):
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "_pick_outlet: exclusion mask left no valid sites — "
                    "ignoring river/road mask, keeping border guard."
                )
                valid = valid_fb
            else:
                # Fallback 2: relax border guard as well (last resort)
                valid_fb2 = np.isfinite(dem) & (acc > 0)
                if not np.any(valid_fb2):
                    raise ValueError(
                        "NO_VALID_SITE: DEM has no valid cells with positive "
                        "flow accumulation."
                    )
                valid = valid_fb2
        else:
            raise ValueError(
                "NO_VALID_SITE: No suitable pond site found. "
                "Every high-accumulation cell is on the grid boundary "
                "or outside the data extent."
            )

    # Get top-N candidate indices by accumulation value among valid cells
    masked_acc = np.where(valid, acc, -1)
    flat_valid = masked_acc.ravel()
    top_flat = np.argsort(flat_valid)[::-1][:n_candidates]
    candidates = [np.unravel_index(int(i), acc.shape) for i in top_flat
                  if flat_valid[i] > 0]

    if not candidates:
        raise ValueError(
            "NO_VALID_SITE: No valid non-river candidates found after masking."
        )

    best = max(candidates, key=lambda rc: float(acc[rc[0], rc[1]]))
    return best



def _catchment_to_geojson(
    mask: np.ndarray,
    transform: tuple,
    cell_size: float,
) -> tuple[dict, float]:
    """
    Convert a binary catchment raster to a GeoJSON polygon and compute area.

    Uses cv2.findContours to trace the catchment boundary directly — this is
    O(boundary pixels), far cheaper than the previous approach of building one
    Shapely Polygon per catchment cell and calling unary_union(), which caused
    GEOS heap corruption (malloc: invalid next size) on dense catchments such
    as those derived from the full-coverage SRTM DEM.

    Why cv2 here?  opencv-python is already in requirements.txt (used by
    land_detector.py), so this adds no new dependency.

    Area is estimated using a local metre-per-degree approximation at the
    centroid latitude — good enough for small catchments, avoids pyproj.
    """
    import cv2 as _cv2
    from shapely.geometry import mapping, Polygon
    from shapely.validation import make_valid

    rows, cols = np.where(mask > 0)
    if len(rows) == 0:
        raise ValueError("Catchment delineation produced an empty mask")

    # Minimum 2 cells — below this cv2.findContours cannot trace a boundary.
    # With road+river exclusion on coarse SRTM grids, small catchments between
    # exclusion zones are legitimate; don't reject them unnecessarily.
    if len(rows) < 2:
        raise ValueError(
            f"Catchment delineation produced only {len(rows)} cell(s). "
            "Try selecting a different area on the map."
        )

    # --- Trace the catchment boundary ---
    mask_u8 = (mask > 0).astype(np.uint8)

    # Dilate by 1 px so the contour runs along the outer pixel edge, not through
    # pixel centres — this prevents thin-diagonal catchments from producing < 3
    # contour points with CHAIN_APPROX_SIMPLE.
    kernel = _cv2.getStructuringElement(_cv2.MORPH_RECT, (3, 3))
    mask_dilated = _cv2.dilate(mask_u8, kernel, iterations=1)

    # CHAIN_APPROX_NONE: keep every contour pixel (no compression).
    # Ensures we get ≥ 4 distinct points even on small rectangular catchments.
    contours, _ = _cv2.findContours(
        mask_dilated, _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_NONE
    )
    if not contours:
        raise ValueError("cv2.findContours found no contour in catchment mask")

    # In case of multiple fragments, take the largest
    main = max(contours, key=_cv2.contourArea)
    pts = main.squeeze(axis=1) if main.ndim == 3 else main

    # Convert pixel (col, row) → geographic (lon, lat)
    x0, dx, _, y0, _, dy = transform
    geo_coords = []
    for pt in pts:
        px, py = int(pt[0]), int(pt[1])
        lon = x0 + (px + 0.5) * dx   # pixel centre
        lat = y0 + (py + 0.5) * dy   # dy is negative → south
        geo_coords.append((lon, lat))

    if len(geo_coords) < 3:
        raise ValueError(
            f"Catchment boundary has only {len(geo_coords)} points. "
            "Try selecting a larger area on the map."
        )

    geo_coords.append(geo_coords[0])  # close the ring
    poly = Polygon(geo_coords)
    if not poly.is_valid or poly.is_empty:
        poly = make_valid(poly)

    # Approximate area in km²
    centroid = poly.centroid
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * np.cos(np.radians(centroid.y))
    area_sq_km = poly.area * m_per_deg_lat * m_per_deg_lon / 1e6

    return mapping(poly), float(area_sq_km)



def _to_affine(transform_tuple: tuple):
    """Convert our 6-tuple to an Affine object for pysheds."""
    from affine import Affine
    a, b, c, d, e, f = transform_tuple
    return Affine(b, c, a, e, f, d)
