"""
FastAPI application entrypoint.

Registers routes, sets up CORS (needed once the React frontend connects in
Phase 3), and provides a health-check root endpoint.
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes.contour import router as contour_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-25s  %(levelname)-7s  %(message)s",
)

app = FastAPI(
    title="Village Pond Planning System — Phase 2 API",
    description="Upload a KML/KMZ contour file to get catchment analysis and pond site recommendation.",
    version="0.1.0",
)

# Phase 3 will need CORS for the React frontend; allow all origins for now.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(contour_router)


@app.get("/", include_in_schema=False)
async def health():
    return {"status": "ok", "phase": 2}


# Consistent error shape for unhandled HTTPExceptions (rules.md §5)
@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    logging.getLogger(__name__).exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={"status": "error", "detail": "Internal server error", "code": "INTERNAL"},
    )
