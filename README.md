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
│   └── README.md     → Backend-specific setup & API docs
└── contours_1m.kml   → Sample contour file for testing
```

## Quick Start

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## Test the API

```bash
curl -X POST http://localhost:8000/analyzeContour \
  -F "file=@contours_1m.kml"
```

Interactive docs at **http://localhost:8000/docs**

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