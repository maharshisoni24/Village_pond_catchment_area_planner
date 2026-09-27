"""
Tunable constants for the catchment analysis pipeline.

All thresholds live here — never hardcoded in service modules — so the system
works generically across different contour files without source changes.
"""

# --- DEM interpolation ---

# Grid cell size in degrees.  ~30 m at Indian latitudes (≈ 0.00027°).
# Finer grids give smoother flow but cost O(n²) memory; 30 m is the sweet spot
# matching SRTM-class resolution and what pysheds handles comfortably.
GRID_CELL_SIZE_DEG: float = 0.00027

# Method for scipy.interpolate.griddata: "linear" is fast and stable for
# contour-derived points; "cubic" can overshoot between sparse contours.
INTERPOLATION_METHOD: str = "linear"


# --- River / stream detection ---

# Any cell whose upstream contributing area exceeds this value (in m²) is
# classified as a likely river/stream channel.  This is a *generic default*,
# not a value fitted to the sample file.
#
# Rationale: ~50 000 m² (5 hectares / 0.05 km²) is a common threshold in
# small-watershed hydrology for distinguishing channelised flow from hillslope
# runoff.  Adjust up for flatter terrain or coarser DEMs.
RIVER_ACCUMULATION_THRESHOLD_SQM: float = 50_000.0

# Buffer distance (metres) around explicitly-tagged river geometry.
# Prevents picking a pond site right at the bank of a tagged river line.
RIVER_BUFFER_M: float = 30.0

# Number of DEM grid cells to dilate the river mask before siting the pond.
# Prevents placing ponds immediately adjacent to a river channel.
# 3 cells × ~111m (SRTM) ≈ 333m buffer; × ~30m (KML) ≈ 90m buffer.
RIVER_EXCLUSION_BUFFER_CELLS: int = 3

# Overpass API timeout in seconds for built-up area queries.
OVERPASS_TIMEOUT_S: int = 25

# Keywords (case-insensitive) that flag a KML placemark or folder as a
# river/stream layer rather than an elevation contour.
RIVER_KEYWORDS: list[str] = [
    "river", "stream", "nala", "drain", "waterway", "canal", "creek",
]


# --- Upload limits ---

MAX_UPLOAD_BYTES: int = 20 * 1024 * 1024  # 20 MB
ALLOWED_EXTENSIONS: set[str] = {".kml", ".kmz"}


# --- External APIs ---

import os as _os

# Mapbox token — loaded from backend/.env via python-dotenv in main.py.
MAPBOX_ACCESS_TOKEN: str = _os.environ.get("MAPBOX_ACCESS_TOKEN", "")

# Satellite imagery provider for land_detector.py.
#
# "mapbox" — Mapbox Static Images API (satellite-v9 style).
#             Requires a valid MAPBOX_ACCESS_TOKEN.  5 000 req/month on demo.
#             Switch TO this when the Mapbox token is working.
#
# "esri"   — ESRI World Imagery (ArcGIS Online).
#             Free, no key, works even when Mapbox limit is hit.
#             Switch TO this when Mapbox returns 401.
#
# ↓↓↓ CHANGE THIS LINE TO SWITCH PROVIDER ↓↓↓
SATELLITE_PROVIDER: str = "mapbox"
# ↑↑↑ options: "mapbox" | "esri" ↑↑↑
