"""
Pydantic response models matching the JSON shape in Architecture.md §5.

Kept in models/ so route handlers stay thin — they just call services and
return these schemas.
"""

from pydantic import BaseModel


class PondLocation(BaseModel):
    lat: float
    lon: float


class CatchmentInfo(BaseModel):
    area_sq_km: float
    boundary_geojson: dict  # GeoJSON Polygon


class TerrainStats(BaseModel):
    elevation_min_m: float
    elevation_max_m: float
    contour_lines_used: int


class RiverCheck(BaseModel):
    river_detected: bool
    detection_method: str  # "explicit_kml_layer" | "derived_flow_accumulation" | "none"
    pond_site_on_river: bool
    nearest_river_distance_m: float | None = None


class AnalyzeContourResponse(BaseModel):
    status: str  # "success"
    pond_location: PondLocation
    catchment: CatchmentInfo
    terrain_stats: TerrainStats
    river_check: RiverCheck
    method_notes: str


class ErrorResponse(BaseModel):
    status: str = "error"
    detail: str
    code: str
