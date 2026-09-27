"""
POST /analyzeContour — the single Phase 2 endpoint, extended for Phase 3.

Accepts a KML/KMZ upload and returns catchment analysis JSON.
Pass ?format=geojson to get a downloadable GeoJSON FeatureCollection instead.
Pass ?include_contours=true to include contour line GeoJSON for map rendering.
Pass ?include_rainfall=true to fetch historical rainfall and compute runoff/pond sizing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, File, Query, UploadFile, HTTPException
from fastapi.responses import Response
from shapely.geometry import mapping

from app.core.config import MAX_UPLOAD_BYTES, ALLOWED_EXTENSIONS
from app.models.schemas import (
    AnalyzeContourResponse,
    CatchmentInfo,
    ErrorResponse,
    PondLocation,
    PondRecommendation,
    RainfallStats,
    RiverCheck,
    RunoffEstimate,
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
async def analyze_contour(
    file: UploadFile = File(...),
    format: str = Query("json", enum=["json", "geojson"]),
    include_contours: bool = Query(False, description="Include contour GeoJSON for map rendering"),
    include_rainfall: bool = Query(False, description="Fetch rainfall and compute runoff/pond sizing"),
):
    """
    Upload a KML/KMZ contour file → get catchment analysis + pond location.

    Use ?format=geojson to download a GeoJSON FeatureCollection file.
    Use ?include_contours=true to get contour lines for frontend map rendering.
    Use ?include_rainfall=true to get rainfall stats, runoff estimate, and pond recommendation.
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

        # 8. Optional: contour GeoJSON for frontend map rendering
        contours_geojson = None
        if include_contours:
            contours_geojson = _build_contours_geojson(parsed.contours)

        # 9. Optional: rainfall + runoff + pond recommendation
        rainfall_data = None
        runoff_data = None
        pond_rec = None
        if include_rainfall:
            try:
                from app.services.rainfall import fetch_rainfall
                from app.services.pond_recommender import estimate_runoff, recommend_pond

                rain = await fetch_rainfall(result["pond_lat"], result["pond_lon"])
                rainfall_data = RainfallStats(**rain)

                runoff = estimate_runoff(
                    catchment_area_sq_km=round(result["area_sq_km"], 4),
                    annual_rainfall_mm=rain["annual_avg_mm"],
                )
                runoff_data = RunoffEstimate(**runoff)

                pond = recommend_pond(annual_runoff_m3=runoff["annual_runoff_m3"])
                pond_rec = PondRecommendation(**pond)
            except Exception as e:
                logger.warning("Rainfall/runoff fetch failed (non-fatal): %s", e)
                # Continue without rainfall data — catchment result is still valid

        # --- GeoJSON file download ---
        if format == "geojson":
            fc = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [result["pond_lon"], result["pond_lat"]],
                        },
                        "properties": {
                            "name": "Recommended Pond Site",
                            "type": "pond",
                            "nearest_river_distance_m": round(dist_m, 1) if dist_m else None,
                        },
                    },
                    {
                        "type": "Feature",
                        "geometry": result["catchment_geojson"],
                        "properties": {
                            "name": "Catchment Boundary",
                            "type": "catchment",
                            "area_sq_km": round(result["area_sq_km"], 4),
                        },
                    },
                ],
            }
            stem = Path(file.filename or "result").stem
            return Response(
                content=json.dumps(fc, indent=2),
                media_type="application/geo+json",
                headers={"Content-Disposition": f'attachment; filename="{stem}_result.geojson"'},
            )

        # --- Default JSON response ---
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
            contours_geojson=contours_geojson,
            rainfall=rainfall_data,
            runoff=runoff_data,
            pond_recommendation=pond_rec,
        )

    except ValueError as e:
        # Known pipeline errors (bad file, no valid site, etc.)
        raise HTTPException(status_code=422, detail=str(e))

    except HTTPException:
        raise  # re-raise FastAPI exceptions as-is

    except Exception as e:
        logger.exception("Unexpected error in /analyzeContour")
        raise HTTPException(
            status_code=500,
            detail="Internal processing error. Check server logs for details.",
        )


def _build_contours_geojson(contours) -> dict:
    """Build a GeoJSON FeatureCollection from parsed contour lines."""
    features = []
    for c in contours:
        features.append({
            "type": "Feature",
            "geometry": mapping(c.geometry),
            "properties": {"elevation_m": c.elevation_m},
        })
    return {"type": "FeatureCollection", "features": features}
