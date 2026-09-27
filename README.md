# Village Pond Catchment Area Planner

An AI-assisted web application for identifying optimal rainwater harvesting pond sites in rural India. Draw a rectangle on the map, and the system fetches real elevation data (SRTM 30m), computes D8 flow accumulation, excludes rivers and built-up areas, and returns the **top-3 ranked candidate pond sites** with their catchment boundaries, runoff estimates, and pond sizing recommendations.

**Course:** Computer System Design — IIT Bhilai  
**Status:** Complete (Phase 1–3)

---

## Features

- 🗺️ **Draw on the map** — click-move-click rectangle selection, 0.01°–0.15° per side
- 🏔️ **Live SRTM 30m DEM** — fetched from OpenTopoData (no API key needed)
- 🌊 **River detection & exclusion** — derived from flow accumulation + 3-cell buffer dilation
- 🏗️ **Built-up / road exclusion** — roads and buildings from OSM API v0.6, buffered 33m
- 🏆 **Top-3 candidate sites** — ranked by catchment area, minimum 5-cell separation, selectable in UI
- 🌧️ **Rainfall & runoff** — Open-Meteo historical API, Rational Method (C × P × A)
- 💧 **Pond sizing** — capacity at 40% annual runoff, dimensions at 3m depth
- 🛰️ **Open land detection** — Mapbox / ESRI satellite imagery + OpenCV HSV segmentation
- 📥 **Export** — download analysis as JSON or GeoJSON (selected candidate's boundary)
- 📄 **KML upload mode** — upload your own contour KML/KMZ for local DEM analysis

---

## Demo

| Select Area | Analysis Result | Candidate Sites |
|---|---|---|
| Draw rectangle on map | 3 ranked pond sites appear | Click to switch active candidate |

---

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js 18+
- A Mapbox token (free, 5,000 requests/month) — or use `SATELLITE_PROVIDER=esri` (no key needed)

### 1. Clone

```bash
git clone https://github.com/maharshisoni24/Village_pond_catchment_area_planner.git
cd Village_pond_catchment_area_planner
```

### 2. Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env — add your MAPBOX_ACCESS_TOKEN

# Start server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. Frontend

```bash
cd frontend
npm install

# Configure environment
cp .env.example .env
# Edit .env — set VITE_API_URL and VITE_MAPBOX_TOKEN

# Start dev server
npm run dev -- --host
```

Both services are now accessible on your local network:
- **Frontend:** `http://<your-ip>:5173`
- **API:** `http://<your-ip>:8000`
- **Swagger docs:** `http://<your-ip>:8000/docs`

---

## Project Structure

```
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   └── routes/
│   │   │       ├── analysis.py      # POST /api/analysis/run — main map-mode endpoint
│   │   │       ├── contour.py       # POST /analyzeContour — KML upload endpoint
│   │   │       ├── land.py          # POST /api/land/detect — open land detection
│   │   │       └── rainfall.py      # GET  /api/rainfall — raw rainfall data
│   │   ├── services/
│   │   │   ├── dem_fetcher.py       # SRTM 30m via OpenTopoData (batch HTTP/1.1)
│   │   │   ├── dem_builder.py       # Contour → DEM interpolation (scipy griddata)
│   │   │   ├── kml_parser.py        # KML/KMZ parsing (lxml, 4 encoding fallbacks)
│   │   │   ├── catchment.py         # pysheds D8 flow → catchment polygon
│   │   │   ├── river_detector.py    # River mask from flow accumulation + KML layer
│   │   │   ├── urban_detector.py    # Road/building exclusion mask (OSM API v0.6)
│   │   │   ├── land_detector.py     # Open land via satellite + OpenCV HSV
│   │   │   ├── rainfall.py          # Open-Meteo Archive API
│   │   │   └── pond_recommender.py  # Runoff estimation + pond sizing
│   │   ├── models/
│   │   │   └── schemas.py           # Pydantic request/response models
│   │   └── core/
│   │       └── config.py            # All tunable constants (buffer cells, thresholds…)
│   ├── requirements.txt
│   ├── .env.example                 # Template — copy to .env
│   └── app/tests/
│       ├── test_catchment.py        # 12 catchment pipeline tests
│       └── test_kml_parser.py       # 10 KML parser tests
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── MapView.jsx          # Leaflet map, rectangle draw, result layers
│   │   │   ├── InputPanel.jsx       # Mode switch, file upload, detect land button
│   │   │   └── ResultDashboard.jsx  # Candidate picker, stats, download buttons
│   │   ├── services/
│   │   │   └── api.js               # Axios wrappers for all endpoints
│   │   └── utils/
│   │       └── contourColors.js     # Elevation → colour mapping for contour lines
│   ├── .env.example                 # Template — copy to .env
│   ├── index.html
│   └── vite.config.js
│
├── contours_1m.kml                  # Sample contour file for KML mode testing
└── README.md
```

---

## How It Works

### Map Mode (Primary)

```
Draw rectangle → POST /api/analysis/run
    ↓
1.  Fetch SRTM 30m DEM (OpenTopoData, 40×40 grid, 16 batches)
2.  Condition DEM: fill pits → fill depressions → resolve flats (pysheds)
3.  D8 flow direction → flow accumulation
4.  Build river mask: threshold-based from accumulation + KML explicit rivers
    → dilate mask by 3 cells (~330m buffer)
5.  Build built-up mask: OSM API v0.6 road/building geometries → rasterise
    → 80% safety valve: drop built-up mask if combined exclusion > 80%
6.  Pick top-3 outlet cells with ≥5-cell separation (progressive fallback: 5→3→1)
7.  Run pysheds catchment delineation from each outlet (forced_outlet)
8.  Fetch rainfall: Open-Meteo Archive API (annual avg + monsoon months)
9.  Estimate runoff: Rational Method → pond capacity → dimensions
    ↓
Return: pond_location, catchment, terrain_stats, river_check,
        rainfall, runoff, pond_recommendation, candidates[3]
```

### KML Mode

```
Upload KML/KMZ → POST /analyzeContour
    ↓
1.  Parse KML: extract contour LineStrings + elevation values (4 encoding fallbacks)
2.  Build local DEM: scipy griddata interpolation from contour vertices
3.  Same pipeline as steps 2–9 above (no SRTM fetch needed)
```

---

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/analysis/run` | POST | Full analysis from a drawn bounding box |
| `/analyzeContour` | POST | Full analysis from uploaded KML/KMZ contour file |
| `/api/land/detect` | POST | Open land + built-up detection for a bounding box |
| `/api/rainfall` | GET | Raw rainfall stats for lat/lon |
| `/docs` | GET | Swagger UI — interactive testing |

### Example: Map Mode

```bash
curl -X POST http://localhost:8000/api/analysis/run \
  -H "Content-Type: application/json" \
  -d '{
    "bbox": [81.28, 21.24, 81.31, 21.27],
    "include_rainfall": true
  }'
```

### Example: KML Mode

```bash
curl -X POST "http://localhost:8000/analyzeContour?include_contours=true&include_rainfall=true" \
  -F "file=@contours_1m.kml"
```

### Example: Open Land Detection

```bash
curl -X POST http://localhost:8000/api/land/detect \
  -H "Content-Type: application/json" \
  -d '{"bbox": [81.28, 21.24, 81.31, 21.27]}'
```

---

## Configuration

All tunable constants live in [`backend/app/core/config.py`](backend/app/core/config.py):

| Constant | Default | Effect |
|---|---|---|
| `SATELLITE_PROVIDER` | `"mapbox"` | `"mapbox"` or `"esri"` (no key) for land detection |
| `RIVER_EXCLUSION_BUFFER_CELLS` | `3` | Buffer cells around detected rivers (~330m at SRTM 30m) |
| `ROAD_BUFFER_DEG` | `0.0003` | Buffer around OSM highway ways (~33m each side) |
| `GRID_RESOLUTION` | `40` | DEM grid size (40×40 = 1600 cells) |
| `RUNOFF_COEFFICIENT` | `0.45` | Rational Method C factor |
| `POND_FILL_FRACTION` | `0.40` | Fraction of annual runoff used for pond sizing |

---

## Environment Variables

### Backend (`backend/.env`)

| Variable | Required | Description |
|---|---|---|
| `MAPBOX_ACCESS_TOKEN` | If using Mapbox | Mapbox API token for satellite imagery |
| `SATELLITE_PROVIDER` | No (default: `mapbox`) | `"mapbox"` or `"esri"` |

### Frontend (`frontend/.env`)

| Variable | Required | Description |
|---|---|---|
| `VITE_API_URL` | No (default: `http://localhost:8000`) | Backend URL |
| `VITE_MAPBOX_TOKEN` | If using Mapbox | Same token as backend |

---

## Tests

```bash
cd backend
source venv/bin/activate
pytest app/tests/ -v
# 22 tests — all passing
```

Tests cover: KML parsing (10 cases), catchment pipeline (12 cases) including edge cases for empty DEM, all-river mask, tiny catchments, coordinate snapping, and the forced-outlet multi-candidate path.

---

## Network Notes (IIT Bhilai Campus)

- **Overpass API** (all mirrors): blocked by campus proxy — use OSM API v0.6 instead
- **HTTP/2**: blocked by campus proxy ALPN negotiation — all `httpx.AsyncClient` calls use `http2=False`
- **OpenTopoData**: works (plain HTTP/1.1 batches)
- **Open-Meteo**: works
- **OSM API v0.6** (`api.openstreetmap.org`): works with `http2=False`

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Backend framework | FastAPI + Uvicorn | Async, fast, auto-docs |
| DEM source | OpenTopoData (SRTM 30m) | Free, no key, batch HTTP/1.1 |
| Hydrology | pysheds | D8 flow direction, catchment delineation |
| Geo operations | Shapely, affine | Road buffers, polygon union/difference |
| DEM conditioning | SciPy griddata | Contour → grid interpolation (KML mode) |
| Image analysis | OpenCV (HSV) | Satellite → open land segmentation |
| Elevation data | SRTM 30m via OpenTopoData | Free, global, ~111m/cell |
| Rainfall data | Open-Meteo Archive API | Free, no key, hourly historical |
| Map tiles | OpenStreetMap (display) | Free |
| Satellite imagery | Mapbox Static Images API / ESRI | Land detection |
| Road/building data | OSM API v0.6 | Free, no key |
| Frontend | React 18 + Vite | Fast build, HMR |
| Map rendering | Leaflet | Lightweight, flexible |
| HTTP client | httpx (async) + axios | Async backend, axios frontend |

---

## License

MIT