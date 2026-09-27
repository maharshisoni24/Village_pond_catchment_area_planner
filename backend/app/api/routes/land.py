"""
POST /api/land/detect — open land identification via OpenCV + OSM built-up exclusion.

Accepts a bounding box and returns:
  - open_land_areas: GeoJSON patches of bare soil / sparse vegetation
    (after subtracting OSM-confirmed built-up areas)
  - built_up_areas: GeoJSON polygons of OSM buildings / residential zones
    (for frontend overlay display)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from shapely.geometry import shape
from shapely.ops import unary_union

from app.services.land_detector import detect_open_land
from app.services.urban_detector import detect_built_up
from app.services.dem_fetcher import _make_meta  # minimal meta for rasterisation
from app.models.schemas import LandDetectResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class LandDetectRequest(BaseModel):
    bbox: list[float]  # [lon_min, lat_min, lon_max, lat_max]

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, v: list[float]) -> list[float]:
        if len(v) != 4:
            raise ValueError("bbox must have exactly 4 values: [lon_min, lat_min, lon_max, lat_max]")
        lon_min, lat_min, lon_max, lat_max = v
        if lon_min >= lon_max or lat_min >= lat_max:
            raise ValueError("bbox: lon_min < lon_max and lat_min < lat_max required")
        if not (-180 <= lon_min <= 180 and -180 <= lon_max <= 180):
            raise ValueError("longitude must be in [-180, 180]")
        if not (-90 <= lat_min <= 90 and -90 <= lat_max <= 90):
            raise ValueError("latitude must be in [-90, 90]")
        return v


@router.post("/land/detect", response_model=LandDetectResponse)
async def detect_land(body: LandDetectRequest):
    """
    Detect open land patches and OSM built-up areas for a bounding box.

    Open land patches are clipped against built-up polygons so settled areas
    are not suggested for pond excavation.
    """
    bbox = body.bbox
    try:
        # 1. Satellite imagery → open land patches
        land_result = await detect_open_land(bbox)

        # 2. Overpass → built-up polygons + raster mask (non-fatal)
        #    _make_meta builds a minimal grid meta for rasterisation
        meta = _make_meta(bbox)
        _, built_up_geojson = await detect_built_up(bbox, meta)

        # 3. Subtract built-up from open land polygons using Shapely
        open_features = land_result["open_land_areas"]["features"]
        if built_up_geojson and open_features:
            try:
                built_up_union = unary_union(
                    [shape(f["geometry"]) for f in built_up_geojson]
                ).buffer(0)  # fix any invalid geometries

                clipped = []
                for feat in open_features:
                    geom = shape(feat["geometry"])
                    try:
                        diff = geom.difference(built_up_union)
                        if diff.is_empty or diff.area == 0:
                            continue
                        from shapely.geometry import mapping
                        clipped.append({
                            "type": "Feature",
                            "geometry": mapping(diff),
                            "properties": feat["properties"],
                        })
                    except Exception:
                        clipped.append(feat)  # keep original on error
                open_features = clipped
                logger.info(
                    "Land detect: %d open patches after built-up subtraction",
                    len(open_features),
                )
            except Exception as exc:
                logger.warning("Built-up subtraction failed (non-fatal): %s", exc)

        return LandDetectResponse(
            status="success",
            open_land_areas={"type": "FeatureCollection", "features": open_features},
            patch_count=len(open_features),
            total_area_m2=land_result["total_area_m2"],
            method_notes=land_result["method_notes"],
            built_up_areas={
                "type": "FeatureCollection",
                "features": built_up_geojson,
            },
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Unexpected error in /api/land/detect")
        raise HTTPException(
            status_code=500,
            detail="Land detection failed. Check server logs.",
        )
