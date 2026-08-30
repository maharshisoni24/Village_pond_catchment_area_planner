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
from shapely.geometry import shape, mapping, MultiPolygon, Polygon

from app.services.kml_parser import ParseResult


def run_catchment_analysis(
    dem: np.ndarray,
    meta: dict,
    river_mask: np.ndarray | None = None,
) -> dict:
    """
    Full catchment pipeline on the interpolated DEM.

    Parameters
    ----------
    dem        : 2-D elevation array (NaN outside data extent).
    meta       : grid metadata from dem_builder (transform, shape, etc.).
    river_mask : boolean array, True = river cell to exclude from pond siting.
                 Same shape as dem.  None if no river info available.

    Returns
    -------
    dict with keys:
        pond_lat, pond_lon     – chosen outlet location
        catchment_geojson      – GeoJSON Polygon of the catchment boundary
        area_sq_km             – catchment area
        flow_accumulation      – raw accumulation grid (for river_detector)
    """
    cell_size = meta["cell_size"]
    transform = meta["transform"]

    # --- Set up pysheds Grid ---
    grid = Grid()

    # pysheds needs a ViewFinder to georeference the raster, and its
    # methods expect Raster objects (numpy subclass with .nodata attribute),
    # not plain numpy arrays.
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

    # 1. Fill pits — removes small artefact depressions that trap flow
    pit_filled = grid.fill_pits(dem_raster)

    # 2. Fill depressions (larger sinks)
    flooded = grid.fill_depressions(pit_filled)

    # 3. Resolve flats — assigns flow direction to perfectly flat areas
    inflated = grid.resolve_flats(flooded)

    # 4. D8 flow direction
    fdir = grid.flowdir(inflated)

    # 5. Flow accumulation — each cell's value = number of upstream cells
    acc = grid.accumulation(fdir)

    # 6. Pick outlet = highest accumulation outside the river mask and data-hole mask
    outlet_row, outlet_col = _pick_outlet(acc, dem, river_mask)

    # Convert pixel to geographic coordinates
    pond_lon = transform[0] + (outlet_col + 0.5) * transform[1]
    pond_lat = transform[3] + (outlet_row + 0.5) * transform[5]

    # 7. Delineate catchment draining to the outlet
    catch_mask = grid.catchment(
        x=pond_lon, y=pond_lat, fdir=fdir, xytype="coordinate"
    )

    # 8. Convert boolean catchment raster → polygon
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


def _pick_outlet(
    acc: np.ndarray,
    dem: np.ndarray,
    river_mask: np.ndarray | None,
) -> tuple[int, int]:
    """
    Choose the best pond outlet: the cell with the highest flow accumulation
    that is NOT on a river and NOT a NaN/nodata cell.

    Why highest-accumulation-outside-river?  The point with the most upstream
    contributing area collects the most runoff — ideal for a pond — but if
    that point is on the main river channel it's unsuitable, so we skip it
    and take the next best candidate (usually a tributary mouth or gully).
    """
    valid = np.isfinite(dem) & (acc > 0)
    if river_mask is not None:
        valid = valid & ~river_mask

    if not np.any(valid):
        raise ValueError(
            "NO_VALID_SITE: No suitable non-river pond site found. "
            "Every high-accumulation cell is either on a detected river "
            "channel or outside the data extent."
        )

    # Mask out invalid cells, then find argmax
    masked_acc = np.where(valid, acc, -1)
    flat_idx = int(np.argmax(masked_acc))
    return np.unravel_index(flat_idx, acc.shape)


def _catchment_to_geojson(
    mask: np.ndarray,
    transform: tuple,
    cell_size: float,
) -> tuple[dict, float]:
    """
    Convert a binary catchment raster to a GeoJSON polygon and compute area.

    Uses a simple contour-tracing approach: find all '1' cells, build a convex
    hull (or union of cell polygons for concave shapes).

    Area is estimated using a local meter-per-degree approximation at the
    centroid latitude — good enough for small catchments, avoids pulling in
    pyproj.
    """
    rows, cols = np.where(mask > 0)
    if len(rows) == 0:
        raise ValueError("Catchment delineation produced an empty mask")

    # Build polygons from each catchment cell — then union them
    cell_polys = []
    x0, dx, _, y0, _, dy = transform
    for r, c in zip(rows, cols):
        x = x0 + c * dx
        y = y0 + r * dy
        cell_polys.append(Polygon([
            (x, y), (x + dx, y), (x + dx, y + dy), (x, y + dy),
        ]))

    from shapely.ops import unary_union
    catchment_poly = unary_union(cell_polys)

    # Simplify to reduce coordinate count (tolerance in degrees ≈ ~5 m)
    catchment_poly = catchment_poly.simplify(cell_size * 0.5)

    if isinstance(catchment_poly, MultiPolygon):
        # Take the largest polygon
        catchment_poly = max(catchment_poly.geoms, key=lambda p: p.area)

    # Approximate area in km²
    centroid = catchment_poly.centroid
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * np.cos(np.radians(centroid.y))
    area_deg2 = catchment_poly.area
    area_m2 = area_deg2 * m_per_deg_lat * m_per_deg_lon
    area_sq_km = area_m2 / 1e6

    geojson = mapping(catchment_poly)
    return geojson, float(area_sq_km)


def _to_affine(transform_tuple: tuple):
    """Convert our 6-tuple to an Affine object for pysheds."""
    from affine import Affine
    a, b, c, d, e, f = transform_tuple
    return Affine(b, c, a, e, f, d)
