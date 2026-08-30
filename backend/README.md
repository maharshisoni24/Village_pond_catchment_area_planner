# Village Pond Planning System — Phase 2 Backend

Catchment analysis API that takes a KML/KMZ contour file and returns a recommended pond location, catchment boundary, and terrain statistics.

## Quick Start

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## API

### `POST /analyzeContour`

Upload a `.kml` or `.kmz` contour file to get catchment analysis.

**Request:** `multipart/form-data` with field `file`

```bash
curl -X POST http://localhost:8000/analyzeContour \
  -F "file=@../contours_1m.kml"
```

**Response (200):**
```json
{
  "status": "success",
  "pond_location": { "lat": 21.262, "lon": 81.294 },
  "catchment": {
    "area_sq_km": 0.008,
    "boundary_geojson": { "type": "Polygon", "coordinates": [...] }
  },
  "terrain_stats": {
    "elevation_min_m": 267.0,
    "elevation_max_m": 298.0,
    "contour_lines_used": 1355
  },
  "river_check": {
    "river_detected": true,
    "detection_method": "derived_flow_accumulation",
    "pond_site_on_river": false,
    "nearest_river_distance_m": 180.3
  },
  "method_notes": "..."
}
```

**Error Response (400/422/500):**
```json
{ "status": "error", "detail": "human-readable message", "code": "PARSE_ERROR" }
```

### `GET /`

Health check → `{"status": "ok", "phase": 2}`

## Interactive API Docs

FastAPI auto-generates OpenAPI docs at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Tests

```bash
python3 -m pytest app/tests/ -v
```

## Project Structure

```
backend/
├── app/
│   ├── main.py                  # FastAPI entrypoint
│   ├── api/routes/contour.py    # POST /analyzeContour handler
│   ├── services/
│   │   ├── kml_parser.py        # KML/KMZ → contour lines + river layers
│   │   ├── dem_builder.py       # Contour interpolation → elevation grid
│   │   ├── catchment.py         # pysheds D8 flow → catchment polygon
│   │   └── river_detector.py    # Explicit + derived river detection
│   ├── models/schemas.py        # Pydantic response models
│   └── core/config.py           # Tunable constants
└── requirements.txt
```

## How It Works

1. **Parse** the KML: extract contour `LineString` geometries + elevation from `<name>` tags
2. **Interpolate** contour vertices into a regular elevation grid (DEM) using `scipy.griddata`
3. **Condition** the DEM: fill pits/depressions, resolve flats (pysheds)
4. **Compute** D8 flow direction and flow accumulation
5. **Detect rivers**: explicit KML layers (if tagged) + derived flow-accumulation threshold
6. **Select pond site**: highest-accumulation cell *outside* the river mask
7. **Delineate catchment** draining to that outlet → GeoJSON polygon + area
