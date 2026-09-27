"""
POST /api/analysis/run — full analysis from a drawn map rectangle (no KML required).

Accepts a bounding box [lon_min, lat_min, lon_max, lat_max] drawn by the user
on the map, fetches DEM from Mapbox Terrain-RGB tiles, then runs the same
catchment → river → rainfall → pond pipeline as /analyzeContour.
"""

from __future__ import annotations

import logging
from typing import Optional

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.core.config import RIVER_EXCLUSION_BUFFER_CELLS
from app.services.dem_fetcher import fetch_dem_for_bbox
from app.services.catchment import run_catchment_analysis, pick_top_outlets
from app.services.river_detector import build_river_mask, nearest_river_distance_m
from app.services.urban_detector import detect_built_up
from app.models.schemas import (
    AnalyzeContourResponse,
    CatchmentInfo,
    ErrorResponse,
    PondCandidate,
    PondLocation,
    PondRecommendation,
    RainfallStats,
    RiverCheck,
    RunoffEstimate,
    TerrainStats,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class AnalysisRunRequest(BaseModel):
    """
    Accepts either:
      - bbox: [lon_min, lat_min, lon_max, lat_max]  — preferred (from rectangle draw)
      - lat + lon + radius_deg                       — fallback (legacy)
    """
    bbox: Optional[list[float]] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    radius_deg: float = 0.04
    include_rainfall: bool = True

    @field_validator("bbox")
    @classmethod
    def valid_bbox(cls, v):
        if v is None:
            return v
        if len(v) != 4:
            raise ValueError("bbox must have 4 values: [lon_min, lat_min, lon_max, lat_max]")
        lon_min, lat_min, lon_max, lat_max = v
        if lon_min >= lon_max or lat_min >= lat_max:
            raise ValueError("bbox: lon_min < lon_max and lat_min < lat_max required")
        return v

    def get_bbox(self) -> list[float]:
        if self.bbox:
            return self.bbox
        if self.lat is not None and self.lon is not None:
            r = self.radius_deg
            return [self.lon - r, self.lat - r, self.lon + r, self.lat + r]
        raise ValueError("Provide either 'bbox' or 'lat'+'lon'")


@router.post(
    "/analysis/run",
    response_model=AnalyzeContourResponse,
    responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def run_analysis(body: AnalysisRunRequest):
    """
    Run full pond-site analysis from a drawn map rectangle.

    Fetches DEM from Mapbox Terrain-RGB tiles for the user-drawn bounding box,
    then runs the same pipeline as /analyzeContour:
    DEM → D8 flow → river detection → outlet selection → catchment delineation
    → (optionally) rainfall + runoff + pond recommendation.
    """
    try:
        bbox = body.get_bbox()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        # 1. Fetch DEM from OpenTopoData SRTM 30m
        dem, meta = await fetch_dem_for_bbox(bbox)

        # 2. First-pass catchment (no exclusion yet — need flow acc for river detection)
        result = run_catchment_analysis(dem, meta, river_mask=None)

        # 3. River detection
        river_mask, detection_method = build_river_mask(
            dem_shape=dem.shape,
            meta=meta,
            flow_accumulation=result["flow_accumulation"],
            explicit_rivers=[],  # no KML rivers in map-click mode
        )

        # 4. Dilate river mask by RIVER_EXCLUSION_BUFFER_CELLS — prevents pond
        #    landing immediately adjacent to a channel.
        if river_mask.any():
            ksize = RIVER_EXCLUSION_BUFFER_CELLS * 2 + 1
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksize, ksize))
            river_mask_dilated = cv2.dilate(
                river_mask.astype(np.uint8), kernel
            ).astype(bool)
        else:
            river_mask_dilated = river_mask

        # 5. Fetch built-up area exclusion mask from OSM (non-fatal)
        built_up_mask, built_up_geojson = await detect_built_up(bbox, meta)

        # 6. Combined exclusion mask: river buffer + roads/built-up.
        #    Safety valve: if combined exclusion > 80% of the grid, drop the
        #    built-up mask — river buffer alone already constrains the site well
        #    enough, and adding roads would leave too few valid cells.
        _MAX_EXCLUSION_FRACTION = 0.80
        exclusion_mask = river_mask_dilated | built_up_mask
        if exclusion_mask.mean() > _MAX_EXCLUSION_FRACTION:
            logger.warning(
                "Combined exclusion %.0f%% exceeds %.0f%% safety threshold — "
                "dropping built-up mask, using river buffer only.",
                100 * exclusion_mask.mean(),
                100 * _MAX_EXCLUSION_FRACTION,
            )
            exclusion_mask = river_mask_dilated
            built_up_geojson = []   # don't show misleading overlay

        # 7. Re-run catchment with combined exclusion mask + build top-3 candidates
        has_exclusion = bool(exclusion_mask.any())
        excl_arg = exclusion_mask if has_exclusion else None

        # Pick top-3 outlets — try progressively looser separation until we get 3.
        # min_sep_cells=5 (≈555m) is ideal; fall back to 3 (≈333m) then 1 (adjacent ok)
        # so we always return 3 candidates even in tight spaces after heavy exclusion.
        top_outlets: list = []
        for sep in (5, 3, 1):
            top_outlets = pick_top_outlets(
                result["flow_accumulation"], dem, excl_arg, n=3, min_sep_cells=sep,
            )
            if len(top_outlets) >= 3:
                break

        # Run catchment for each candidate (forced_outlet skips re-picking)
        candidates: list[PondCandidate] = []
        for rank, (row, col) in enumerate(top_outlets, start=1):
            try:
                c = run_catchment_analysis(
                    dem, meta,
                    river_mask=excl_arg,
                    forced_outlet=(row, col),
                )
                candidates.append(PondCandidate(
                    rank=rank,
                    lat=c["pond_lat"],
                    lon=c["pond_lon"],
                    area_sq_km=round(c["area_sq_km"], 4),
                    boundary_geojson=c["catchment_geojson"],
                ))
            except Exception as exc:
                logger.warning("Candidate %d failed (skipped): %s", rank, exc)

        # Use rank-1 candidate as the primary result if candidates were generated
        if candidates:
            result = {
                "pond_lat": candidates[0].lat,
                "pond_lon": candidates[0].lon,
                "catchment_geojson": candidates[0].boundary_geojson,
                "area_sq_km": candidates[0].area_sq_km,
                "flow_accumulation": result["flow_accumulation"],
            }
        elif has_exclusion:
            result = run_catchment_analysis(dem, meta, river_mask=exclusion_mask)

        # 8. Distance from pond to nearest river (use undilated mask for measurement)
        dist_m = nearest_river_distance_m(
            result["pond_lon"], result["pond_lat"], river_mask, meta
        )

        # 9. Optional: rainfall + runoff + pond sizing
        rainfall_data = None
        runoff_data = None
        pond_rec = None
        if body.include_rainfall:
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
                logger.warning("Rainfall fetch failed (non-fatal): %s", e)

        # Elevation range from DEM
        valid_elev = dem[~np.isnan(dem)]
        elev_min = float(valid_elev.min()) if valid_elev.size else 0.0
        elev_max = float(valid_elev.max()) if valid_elev.size else 0.0

        built_up_count = len(built_up_geojson)
        return AnalyzeContourResponse(
            status="success",
            pond_location=PondLocation(lat=result["pond_lat"], lon=result["pond_lon"]),
            catchment=CatchmentInfo(
                area_sq_km=round(result["area_sq_km"], 4),
                boundary_geojson=result["catchment_geojson"],
            ),
            terrain_stats=TerrainStats(
                elevation_min_m=round(elev_min, 1),
                elevation_max_m=round(elev_max, 1),
                contour_lines_used=0,
                dem_source="SRTM 30m (OpenTopoData)",
            ),
            river_check=RiverCheck(
                river_detected=river_mask.any(),
                detection_method=detection_method,
                pond_site_on_river=False,
                nearest_river_distance_m=round(dist_m, 1) if dist_m else None,
            ),
            method_notes=(
                f"DEM: SRTM 30m via OpenTopoData; "
                f"bbox [{bbox[0]:.4f},{bbox[1]:.4f},{bbox[2]:.4f},{bbox[3]:.4f}]; "
                "D8 flow direction/accumulation via pysheds; "
                f"river buffer {RIVER_EXCLUSION_BUFFER_CELLS} cells; "
                f"built-up exclusion: {built_up_count} OSM road/building features; "
                f"top-3 candidate sites returned; "
                f"river method: {detection_method}."
            ),
            contours_geojson=None,
            rainfall=rainfall_data,
            runoff=runoff_data,
            pond_recommendation=pond_rec,
            candidates=candidates,
        )

    except ValueError as e:
        # Domain-level failure (NO_VALID_SITE, catchment too small, etc.)
        # 400 Bad Request — not a schema error (422), not a server crash (500)
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error in /api/analysis/run")
        raise HTTPException(status_code=500, detail="Analysis failed. Check server logs.")
