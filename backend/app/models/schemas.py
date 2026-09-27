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
    dem_source: str = ""  # "SRTM 30m (OpenTopoData)" in map-selection mode; "" for KML


class RiverCheck(BaseModel):
    river_detected: bool
    detection_method: str  # "explicit_kml_layer" | "derived_flow_accumulation" | "none"
    pond_site_on_river: bool
    nearest_river_distance_m: float | None = None


class RainfallStats(BaseModel):
    annual_avg_mm: float
    monsoon_avg_mm: float
    years: int
    yearly: list[dict] | None = None
    source: str = "open_meteo"  # "open_meteo" | "regional_estimate"


class RunoffEstimate(BaseModel):
    annual_runoff_m3: float
    runoff_coefficient: float


class PondRecommendation(BaseModel):
    target_volume_m3: float
    recommended_depth_m: float
    surface_area_m2: float
    storage_fraction: float


class PondCandidate(BaseModel):
    rank: int                    # 1 = best, 2 = second, 3 = third
    lat: float
    lon: float
    area_sq_km: float
    boundary_geojson: dict       # GeoJSON Polygon


class AnalyzeContourResponse(BaseModel):
    status: str  # "success"
    pond_location: PondLocation
    catchment: CatchmentInfo
    terrain_stats: TerrainStats
    river_check: RiverCheck
    method_notes: str
    contours_geojson: dict | None = None
    rainfall: RainfallStats | None = None
    runoff: RunoffEstimate | None = None
    pond_recommendation: PondRecommendation | None = None
    candidates: list[PondCandidate] = []   # top-3 ranked candidates (map mode)


class ErrorResponse(BaseModel):
    status: str = "error"
    detail: str
    code: str


class LandDetectResponse(BaseModel):
    status: str  # "success"
    open_land_areas: dict   # GeoJSON FeatureCollection (built-up areas subtracted)
    patch_count: int
    total_area_m2: float
    method_notes: str
    built_up_areas: dict = {"type": "FeatureCollection", "features": []}  # OSM buildings/residential
