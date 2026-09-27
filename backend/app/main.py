"""
FastAPI application entrypoint.

Registers routes, sets up CORS (needed once the React frontend connects in
Phase 3), and provides a health-check root endpoint.
"""

import logging
from dotenv import load_dotenv

load_dotenv()  # loads backend/.env so MAPBOX_ACCESS_TOKEN is available

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes.contour import router as contour_router
from app.api.routes.rainfall import router as rainfall_router
from app.api.routes.land import router as land_router
from app.api.routes.analysis import router as analysis_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-25s  %(levelname)-7s  %(message)s",
)

# Suppress httpx/httpcore per-request INFO lines — they flood the terminal
# with every tile URL. We still see WARNING (retries) and ERROR (failures).
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

app = FastAPI(
    title="Village Pond Planning System",
    description="Upload a KML/KMZ contour file to get catchment analysis, rainfall stats, and pond recommendation.",
    version="0.2.0",
)

# Phase 3 will need CORS for the React frontend; allow all origins for now.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(contour_router)
app.include_router(rainfall_router)
app.include_router(land_router)
app.include_router(analysis_router)


@app.get("/", include_in_schema=False)
async def health():
    return {"status": "ok", "phase": 3}


# Consistent error shape for unhandled HTTPExceptions (rules.md §5)
@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    logging.getLogger(__name__).exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={"status": "error", "detail": "Internal server error", "code": "INTERNAL"},
    )
