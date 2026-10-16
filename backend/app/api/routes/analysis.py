"""
POST /api/analysis/run — full analysis from a drawn map rectangle (no KML required).

Accepts a bounding box [lon_min, lat_min, lon_max, lat_max] drawn by the user
on the map, fetches DEM from Mapbox Terrain-RGB tiles, then runs the same
catchment → river → rainfall → pond pipeline as /analyzeContour.

OTel instrumentation: every major pipeline stage gets its own manual span so
the trace waterfall clearly shows where time is spent.  Custom histogram
observations are recorded for each stage so Grafana can plot per-stage
duration distributions.
"""

from __future__ import annotations

import logging
import time
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

# OTel — import after setup_telemetry() has been called in main.py
import app.telemetry as tel

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

    # ── OTel: increment in-flight counter + total-request counter ─────────────
    if tel.inflight_analyses_counter is not None:
        tel.inflight_analyses_counter.add(1, {"endpoint": "/api/analysis/run"})
    if tel.analysis_requests_counter is not None:
        tel.analysis_requests_counter.add(1, {"endpoint": "/api/analysis/run"})

    t_total_start = time.perf_counter()
    logger.info("Analysis started: bbox=%s include_rainfall=%s", bbox, body.include_rainfall)

    try:
        # ── OTel: outer span encompasses entire analysis ───────────────────────
        with tel.tracer.start_as_current_span("pond.analysis.full") as span:
            span.set_attributes({
                "analysis.bbox.lon_min": bbox[0],
                "analysis.bbox.lat_min": bbox[1],
                "analysis.bbox.lon_max": bbox[2],
                "analysis.bbox.lat_max": bbox[3],
                "analysis.include_rainfall": body.include_rainfall,
            })

            # ── Stage 1: DEM fetch ─────────────────────────────────────────────
            with tel.tracer.start_as_current_span("pond.dem_fetch") as dem_span:
                t0 = time.perf_counter()
                logger.info("Stage 1/8: fetching SRTM DEM for bbox=%s", bbox)
                dem, meta = await fetch_dem_for_bbox(bbox)
                dem_dur = time.perf_counter() - t0

                dem_span.set_attributes({
                    "dem.shape.rows": dem.shape[0],
                    "dem.shape.cols": dem.shape[1],
                    "dem.valid_pct": round(float(np.isfinite(dem).mean()) * 100, 1),
                    "dem.elev_min_m": round(float(np.nanmin(dem)), 1),
                    "dem.elev_max_m": round(float(np.nanmax(dem)), 1),
                    "dem.source": "SRTM 30m (OpenTopoData)",
                    "dem.duration_s": round(dem_dur, 3),
                })
                if tel.dem_fetch_duration_hist is not None:
                    tel.dem_fetch_duration_hist.record(dem_dur, {"endpoint": "/api/analysis/run"})

            logger.info("Stage 1 done: DEM %s in %.2f s", dem.shape, dem_dur)

            # ── Stage 2: First-pass catchment (no exclusion) ───────────────────
            with tel.tracer.start_as_current_span("pond.catchment.first_pass") as c_span:
                t0 = time.perf_counter()
                logger.info("Stage 2/8: first-pass catchment delineation")
                result = run_catchment_analysis(dem, meta, river_mask=None)
                c1_dur = time.perf_counter() - t0

                c_span.set_attributes({
                    "catchment.pass": "first",
                    "catchment.area_sq_km": round(result["area_sq_km"], 4),
                    "catchment.pond_lat": round(result["pond_lat"], 5),
                    "catchment.pond_lon": round(result["pond_lon"], 5),
                    "catchment.duration_s": round(c1_dur, 3),
                })
                if tel.catchment_duration_hist is not None:
                    tel.catchment_duration_hist.record(c1_dur, {"pass": "first"})

            logger.info("Stage 2 done: area=%.4f km² in %.2f s", result["area_sq_km"], c1_dur)

            # ── Stage 3: River detection ───────────────────────────────────────
            with tel.tracer.start_as_current_span("pond.river_detection") as r_span:
                t0 = time.perf_counter()
                logger.info("Stage 3/8: river detection")
                river_mask, detection_method = build_river_mask(
                    dem_shape=dem.shape,
                    meta=meta,
                    flow_accumulation=result["flow_accumulation"],
                    explicit_rivers=[],
                )
                r_dur = time.perf_counter() - t0

                r_span.set_attributes({
                    "river.detected": bool(river_mask.any()),
                    "river.detection_method": detection_method,
                    "river.mask_coverage_pct": round(float(river_mask.mean()) * 100, 1),
                    "river.duration_s": round(r_dur, 3),
                })

            logger.info(
                "Stage 3 done: river=%s method=%s in %.2f s",
                river_mask.any(), detection_method, r_dur
            )

            # ── Stage 4: River mask dilation ──────────────────────────────────
            with tel.tracer.start_as_current_span("pond.river_dilation"):
                if river_mask.any():
                    ksize = RIVER_EXCLUSION_BUFFER_CELLS * 2 + 1
                    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksize, ksize))
                    river_mask_dilated = cv2.dilate(
                        river_mask.astype(np.uint8), kernel
                    ).astype(bool)
                else:
                    river_mask_dilated = river_mask

            # ── Stage 5: Built-up area detection (OSM) ────────────────────────
            with tel.tracer.start_as_current_span("pond.built_up_detection") as b_span:
                t0 = time.perf_counter()
                logger.info("Stage 5/8: built-up area detection from OSM")
                built_up_mask, built_up_geojson = await detect_built_up(bbox, meta)
                b_dur = time.perf_counter() - t0

                b_span.set_attributes({
                    "buildup.features_count": len(built_up_geojson),
                    "buildup.mask_coverage_pct": round(float(built_up_mask.mean()) * 100, 1),
                    "buildup.duration_s": round(b_dur, 3),
                })

            logger.info(
                "Stage 5 done: %d built-up features in %.2f s", len(built_up_geojson), b_dur
            )

            # ── Stage 6: Combined exclusion mask ──────────────────────────────
            with tel.tracer.start_as_current_span("pond.exclusion_mask") as ex_span:
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
                    built_up_geojson = []

                ex_span.set_attributes({
                    "exclusion.combined_pct": round(float(exclusion_mask.mean()) * 100, 1),
                    "exclusion.threshold_pct": _MAX_EXCLUSION_FRACTION * 100,
                })

            # ── Stage 7: Multi-candidate catchment delineation ────────────────
            with tel.tracer.start_as_current_span("pond.catchment.multi_candidate") as mc_span:
                t0 = time.perf_counter()
                logger.info("Stage 7/8: multi-candidate catchment delineation")

                has_exclusion = bool(exclusion_mask.any())
                excl_arg = exclusion_mask if has_exclusion else None

                top_outlets: list = []
                for sep in (5, 3, 1):
                    top_outlets = pick_top_outlets(
                        result["flow_accumulation"], dem, excl_arg, n=3, min_sep_cells=sep,
                    )
                    if len(top_outlets) >= 3:
                        break

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
                        logger.debug(
                            "Candidate %d skipped (small catchment in river-heavy area): %s",
                            rank, exc
                        )

                mc_dur = time.perf_counter() - t0
                mc_span.set_attributes({
                    "catchment.pass": "multi_candidate",
                    "catchment.candidates_found": len(candidates),
                    "catchment.outlets_tried": len(top_outlets),
                    "catchment.duration_s": round(mc_dur, 3),
                })
                if tel.catchment_duration_hist is not None:
                    tel.catchment_duration_hist.record(mc_dur, {"pass": "multi_candidate"})

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

                mc_span.set_attributes({
                    "catchment.final_area_sq_km": round(result["area_sq_km"], 4),
                    "catchment.final_lat": round(result["pond_lat"], 5),
                    "catchment.final_lon": round(result["pond_lon"], 5),
                })

            logger.info(
                "Stage 7 done: %d candidates, area=%.4f km² in %.2f s",
                len(candidates), result["area_sq_km"], mc_dur
            )

            # ── Distance to nearest river ─────────────────────────────────────
            dist_m = nearest_river_distance_m(
                result["pond_lon"], result["pond_lat"], river_mask, meta
            )

            # ── Stage 8: Rainfall + runoff + pond recommendation ──────────────
            rainfall_data = None
            runoff_data   = None
            pond_rec      = None
            if body.include_rainfall:
                with tel.tracer.start_as_current_span("pond.rainfall_fetch") as rain_span:
                    t0 = time.perf_counter()
                    logger.info("Stage 8/8: fetching rainfall data")
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
                        rain_dur = time.perf_counter() - t0

                        rain_span.set_attributes({
                            "rainfall.source": rain.get("source", "unknown"),
                            "rainfall.annual_avg_mm": rain["annual_avg_mm"],
                            "rainfall.monsoon_avg_mm": rain["monsoon_avg_mm"],
                            "rainfall.years_analysed": rain.get("years", 0),
                            "runoff.annual_m3": runoff["annual_runoff_m3"],
                            "pond.target_volume_m3": pond["target_volume_m3"],
                            "pond.recommended_depth_m": pond["recommended_depth_m"],
                            "rainfall.duration_s": round(rain_dur, 3),
                        })
                        if tel.rainfall_duration_hist is not None:
                            tel.rainfall_duration_hist.record(
                                rain_dur, {"source": rain.get("source", "unknown")}
                            )
                        logger.info(
                            "Stage 8 done: %.0f mm/yr rainfall, %.0f m³ runoff in %.2f s",
                            rain["annual_avg_mm"], runoff["annual_runoff_m3"], rain_dur
                        )
                    except Exception as e:
                        rain_dur = time.perf_counter() - t0
                        logger.warning("Rainfall fetch failed (non-fatal): %s", e)
                        rain_span.set_attribute("rainfall.error", str(e))

            # ── Build response ─────────────────────────────────────────────────
            valid_elev = dem[~np.isnan(dem)]
            elev_min = float(valid_elev.min()) if valid_elev.size else 0.0
            elev_max = float(valid_elev.max()) if valid_elev.size else 0.0

            built_up_count = len(built_up_geojson)

            # ── OTel: annotate outer span with final summary ───────────────────
            t_total = time.perf_counter() - t_total_start
            span.set_attributes({
                "analysis.total_duration_s": round(t_total, 3),
                "analysis.candidates_found": len(candidates),
                "analysis.final_area_sq_km": round(result["area_sq_km"], 4),
                "analysis.river_detected": bool(river_mask.any()),
                "analysis.elev_range_m": round(elev_max - elev_min, 1),
            })
            if tel.analysis_duration_hist is not None:
                tel.analysis_duration_hist.record(t_total, {"endpoint": "/api/analysis/run"})

            logger.info(
                "Analysis complete: %.3f s total, area=%.4f km², %d candidates",
                t_total, result["area_sq_km"], len(candidates)
            )

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
        if tel.analysis_errors_counter is not None:
            tel.analysis_errors_counter.add(1, {"endpoint": "/api/analysis/run", "error_type": "ValueError"})
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        if tel.analysis_errors_counter is not None:
            tel.analysis_errors_counter.add(1, {"endpoint": "/api/analysis/run", "error_type": type(e).__name__})
        logger.exception("Unexpected error in /api/analysis/run")
        raise HTTPException(status_code=500, detail="Analysis failed. Check server logs.")
    finally:
        # Always decrement in-flight counter
        if tel.inflight_analyses_counter is not None:
            tel.inflight_analyses_counter.add(-1, {"endpoint": "/api/analysis/run"})
