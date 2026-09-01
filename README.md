# AI-based Village Pond Planning System

A web application that analyzes contour/elevation data (KML/KMZ) to identify optimal pond locations using catchment delineation, D8 flow analysis, and river-exclusion logic.

**Course:** Computer System Design  
**Status:** Phase 2 (Backend API) ✅ 

## Project Structure

```
├── backend/          → FastAPI service (Phase 2)
│   ├── app/
│   │   ├── api/      → Route handlers
│   │   ├── services/ → KML parser, DEM builder, catchment engine, river detector
│   │   ├── models/   → Pydantic schemas
│   │   └── core/     → Config & constants
│   └── requirements.txt
└── contours_1m.kml   → Sample contour file for testing
```

## Quick Start

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## API Endpoints (localhost)

| URL | What it does |
|---|---|
| http://localhost:8000/analyzeContour | `POST` — upload a KML/KMZ file |
| http://localhost:8000/docs | Swagger UI — interactive API testing in browser |
| http://localhost:8000/redoc | ReDoc — formatted API documentation |
| http://localhost:8000/ | Health check |

### Test (JSON response)

```bash
curl -X POST http://localhost:8000/analyzeContour \
  -F "file=@contours_1m.kml"
```

### Download GeoJSON file

```bash
curl -X POST "http://localhost:8000/analyzeContour?format=geojson" \
  -F "file=@contours_1m.kml" -o result.geojson
```

---

## 🌐 Live on IIT Bhilai Network

Currently hosted at **`10.1.75.53:7202`** on the IIT Bhilai campus network.

| URL | What it does |
|---|---|
| http://10.1.75.53:7202/analyzeContour | `POST` — upload a KML/KMZ file |
| http://10.1.75.53:7202/docs | Swagger UI — interactive API testing in browser |
| http://10.1.75.53:7202/redoc | ReDoc — formatted API documentation |
| http://10.1.75.53:7202/ | Health check |

### Test (JSON response)

```bash
curl -X POST http://10.1.75.53:7202/analyzeContour \
  -F "file=@contours_1m.kml"
```

### Download GeoJSON file

```bash
curl -X POST "http://10.1.75.53:7202/analyzeContour?format=geojson" \
  -F "file=@contours_1m.kml" -o result.geojson
```

---

## Run Tests

```bash
cd backend
source venv/bin/activate
pytest app/tests/ -v
```

## How It Works

1. **Parse** KML/KMZ → extract contour lines with elevation values
2. **Interpolate** contour vertices → regular elevation grid (DEM)
3. **Condition** DEM → fill pits, resolve flats (pysheds)
4. **Compute** D8 flow direction → flow accumulation
5. **Detect rivers** → explicit KML layer (if tagged) + derived flow threshold
6. **Select pond site** → highest accumulation point *outside* river mask
7. **Delineate catchment** → polygon + area draining to the pond

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, Uvicorn |
| Geo parsing | lxml, Shapely |
| Interpolation | NumPy, SciPy |
| Hydrology | pysheds (D8 flow, catchment delineation) |