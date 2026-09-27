"""
GET /api/rainfall — historical rainfall for a coordinate.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, HTTPException

from app.services.rainfall import fetch_rainfall

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


@router.get("/rainfall")
async def get_rainfall(
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    years: int = Query(5, ge=1, le=30, description="Number of years to look back"),
):
    """Fetch historical rainfall from Open-Meteo for the given coordinates."""
    try:
        return await fetch_rainfall(lat, lon, years)
    except Exception as e:
        logger.exception("Rainfall fetch failed")
        raise HTTPException(status_code=502, detail=f"Rainfall data unavailable: {e}")
