"""
POST /analyzeContour — the single Phase 2 endpoint.

Accepts a KML/KMZ upload and returns catchment analysis JSON.
Thin handler: validates input, calls service modules, assembles response.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, File, UploadFile, HTTPException

from app.core.config import MAX_UPLOAD_BYTES, ALLOWED_EXTENSIONS
from app.models.schemas import (
    AnalyzeContourResponse,
    CatchmentInfo,
    ErrorResponse,
    PondLocation,
    RiverCheck,
    TerrainStats,
)
from app.services.kml_parser import parse_kml_bytes
from app.services.dem_builder import build_dem
from app.services.catchment import run_catchment_analysis
from app.services.river_detector import build_river_mask, nearest_river_distance_m

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/analyzeContour",
    response_model=AnalyzeContourResponse,
    responses={
        400: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def analyze_contour(file: UploadFile = File(...)):
    """
    Upload a KML/KMZ contour file → get catchment analysis + pond location.

    The pipeline:  parse → interpolate → flow analysis → river exclusion → respond.
    Each step is in its own service module under services/.
    """
    # --- Input validation ---
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Accepted: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({len(raw) / 1e6:.1f} MB). Max: {MAX_UPLOAD_BYTES / 1e6:.0f} MB.",
        )
    if len(raw) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # --- Pipeline ---
    try:
        # 1. Parse KML
        parsed = parse_kml_bytes(raw, filename=file.filename or "")

        # 2. Interpolate to DEM grid
        dem, meta = build_dem(parsed.contours)

        # 3. Catchment analysis (first pass — without river mask to get flow acc)
        result = run_catchment_analysis(dem, meta, river_mask=None)

        # 4. River detection (needs flow accumulation from step 3)
        river_mask, detection_method = build_river_mask(
            dem_shape=dem.shape,
            meta=meta,
            flow_accumulation=result["flow_accumulation"],
            explicit_rivers=parsed.rivers,
        )

        # 5. Re-run outlet selection with river mask if rivers were detected
        has_river = bool(river_mask.any())
        if has_river:
            result = run_catchment_analysis(dem, meta, river_mask=river_mask)

        # 6. Distance from pond to nearest river cell
        dist_m = nearest_river_distance_m(
            result["pond_lon"], result["pond_lat"], river_mask, meta
        )

        # 7. Terrain stats from parsed contours
        elevations = [c.elevation_m for c in parsed.contours]

        return AnalyzeContourResponse(
            status="success",
            pond_location=PondLocation(
                lat=result["pond_lat"], lon=result["pond_lon"]
            ),
            catchment=CatchmentInfo(
                area_sq_km=round(result["area_sq_km"], 4),
                boundary_geojson=result["catchment_geojson"],
            ),
            terrain_stats=TerrainStats(
                elevation_min_m=min(elevations),
                elevation_max_m=max(elevations),
                contour_lines_used=len(parsed.contours),
            ),
            river_check=RiverCheck(
                river_detected=has_river,
                detection_method=detection_method,
                pond_site_on_river=False,  # guaranteed by exclusion logic
                nearest_river_distance_m=round(dist_m, 1) if dist_m else None,
            ),
            method_notes=(
                "Elevation interpolated from contour vertices using linear griddata; "
                "D8 flow direction/accumulation computed via pysheds; "
                "pond site chosen as the highest-accumulation point outside the "
                f"detected river mask (method: {detection_method})."
            ),
        )

    except ValueError as e:
        # Known pipeline errors (bad file, no valid site, etc.)
        code = "NO_VALID_SITE" if "NO_VALID_SITE" in str(e) else "PARSE_ERROR"
        raise HTTPException(status_code=422, detail=str(e))

    except Exception as e:
        logger.exception("Unexpected error in /analyzeContour")
        raise HTTPException(
            status_code=500,
            detail="Internal processing error. Check server logs for details.",
        )
