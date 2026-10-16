"""
FastAPI application entrypoint.

Registers routes, sets up CORS (needed once the React frontend connects in
Phase 3), and provides a health-check root endpoint.

Phase 4 (OTel): Initialises telemetry via lifespan so it runs AFTER uvicorn
binds the port (prevents blocking startup on gRPC connection attempts).
"""

import logging
import os as _os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()  # loads backend/.env so MAPBOX_ACCESS_TOKEN is available

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
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan: OTel initialisation runs here, AFTER uvicorn has already
    bound the port.  This prevents the gRPC connection retry loop from delaying
    the health-check response during startup.
    """
    # ── OTel setup (non-blocking — runs in event loop after port is bound) ───
    from app.telemetry import setup_telemetry
    setup_telemetry()

    # ── FastAPIInstrumentor must be applied AFTER the app object exists ───────
    if _os.environ.get("OTEL_ENABLED", "true").lower() not in ("false", "0", "no"):
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor.instrument_app(app, excluded_urls="/")
            _log.info("OTel: FastAPIInstrumentor attached")
        except Exception as exc:
            _log.warning("OTel: FastAPIInstrumentor failed (%s)", exc)

    _log.info("Village Pond backend started — OTel=%s",
              _os.environ.get("OTEL_ENABLED", "true"))
    yield
    # ── Shutdown ──────────────────────────────────────────────────────────────
    _log.info("Village Pond backend shutting down")


app = FastAPI(
    title="Village Pond Planning System",
    description="Upload a KML/KMZ contour file to get catchment analysis, rainfall stats, and pond recommendation.",
    version="0.2.0",
    lifespan=lifespan,
)

# Allow traceparent header so browser OTel can inject distributed-trace headers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*", "traceparent", "tracestate"],
    expose_headers=["traceparent", "tracestate"],
)

app.include_router(contour_router)
app.include_router(rainfall_router)
app.include_router(land_router)
app.include_router(analysis_router)
app.include_router(debug_router)


@app.get("/", include_in_schema=False)
async def health():
    return {"status": "ok", "phase": 3, "otel": _os.environ.get("OTEL_ENABLED", "true")}


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logging.getLogger(__name__).exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={"status": "error", "detail": "Internal server error", "code": "INTERNAL"},
    )
