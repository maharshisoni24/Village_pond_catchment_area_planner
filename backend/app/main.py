"""
FastAPI application entrypoint.

Registers routes, sets up CORS (needed once the React frontend connects in
Phase 3), and provides a health-check root endpoint.

Phase 4 (OTel): Initialises telemetry at startup via app/telemetry.py so that
every request is automatically traced, metered, and logged.
"""

import logging
from dotenv import load_dotenv

load_dotenv()  # loads backend/.env so MAPBOX_ACCESS_TOKEN is available

# ── OTel: initialise BEFORE importing FastAPI instrumentation ──────────────────
# setup_telemetry() wires TracerProvider / MeterProvider / LoggerProvider and
# attaches a LoggingHandler to Python's root logger so all subsequent
# logging.getLogger(…) calls export to the OTel Collector automatically.
from app.telemetry import setup_telemetry
setup_telemetry()
# ──────────────────────────────────────────────────────────────────────────────

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes.contour import router as contour_router
from app.api.routes.rainfall import router as rainfall_router
from app.api.routes.land import router as land_router
from app.api.routes.analysis import router as analysis_router
from app.api.routes.debug import router as debug_router  # demo endpoints for viva

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

# ── OTel: auto-instrument FastAPI (HTTP server spans + metric attributes) ──────
# FastAPIInstrumentor wraps every route so every request automatically gets:
#   - A root server span (http.method, http.route, http.status_code)
#   - Propagated W3C traceparent headers from the browser frontend
# Must be called AFTER FastAPI() is created.
import os as _os
if _os.environ.get("OTEL_ENABLED", "true").lower() not in ("false", "0", "no"):
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(
            app,
            # Exclude health-check from traces to keep trace list clean
            excluded_urls="/",
        )
        logging.getLogger(__name__).info("OTel: FastAPIInstrumentor attached")
    except Exception as _exc:
        logging.getLogger(__name__).warning(
            "OTel: FastAPIInstrumentor failed (%s) — continuing without auto-instrumentation", _exc
        )
# ──────────────────────────────────────────────────────────────────────────────

# Phase 3 will need CORS for the React frontend; allow all origins for now.
# expose `traceparent` so browser OTel can inject distributed-trace headers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*", "traceparent", "tracestate"],   # W3C trace-context propagation
    expose_headers=["traceparent", "tracestate"],
)

app.include_router(contour_router)
app.include_router(rainfall_router)
app.include_router(land_router)
app.include_router(analysis_router)
app.include_router(debug_router)   # /debug/slow  /debug/error  /debug/health


@app.get("/", include_in_schema=False)
async def health():
    return {"status": "ok", "phase": 3, "otel": _os.environ.get("OTEL_ENABLED", "true")}


# Consistent error shape for unhandled HTTPExceptions (rules.md §5)
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logging.getLogger(__name__).exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={"status": "error", "detail": "Internal server error", "code": "INTERNAL"},
    )
