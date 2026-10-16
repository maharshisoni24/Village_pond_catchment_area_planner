"""
Debug / demo endpoints for OTel viva demonstration.

These endpoints exist ONLY to produce interesting telemetry:
  GET  /debug/health  — simple health check (always fast, always 200)
  GET  /debug/slow    — sleeps 3 s so Jaeger shows a slow span (adjustable via ?delay=)
  GET  /debug/error   — deliberately raises an exception so a failed trace appears

They are clearly labelled as demo endpoints and should NOT be used in production.
The ?delay= param on /debug/slow lets you tune the duration live during the viva.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

import app.telemetry as tel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/debug", tags=["[OTel Demo]"])


@router.get("/health")
async def debug_health():
    """Always returns 200 — proves the backend is alive."""
    return {"status": "ok", "message": "Village Pond backend is healthy"}


@router.get("/slow")
async def debug_slow(
    delay: float = Query(
        default=3.0, ge=0.1, le=30.0,
        description="How many seconds to sleep (0.1–30). Default 3 s.",
    )
):
    """
    DEMO ENDPOINT — sleeps `delay` seconds to produce a visibly slow span in Jaeger.

    Shows:
    - A span whose duration is clearly > threshold
    - Span attribute `demo.delay_s` with the requested delay
    - A span event marking the start of the artificial wait
    """
    tracer = tel.tracer

    # Use a named manual span so it appears separately in the trace waterfall
    with tracer.start_as_current_span("demo.artificial_delay") as span:
        span.set_attribute("demo.delay_s", delay)
        span.set_attribute("demo.endpoint", "/debug/slow")
        span.add_event("starting_artificial_wait", {"delay_s": delay})

        logger.info("[OTel Demo] /debug/slow: sleeping %.1f s", delay)
        t0 = time.perf_counter()
        await asyncio.sleep(delay)
        elapsed = time.perf_counter() - t0

        span.add_event("artificial_wait_complete", {"elapsed_s": round(elapsed, 3)})
        span.set_attribute("demo.actual_elapsed_s", round(elapsed, 3))

    logger.info("[OTel Demo] /debug/slow: completed in %.3f s", elapsed)
    return {
        "status": "ok",
        "message": f"Slept {elapsed:.2f} s — check Jaeger for the slow span",
        "demo": True,
    }


@router.get("/error")
async def debug_error(
    message: str = Query(
        default="Simulated analysis failure",
        description="Custom error message to embed in the exception.",
    )
):
    """
    DEMO ENDPOINT — deliberately raises an exception so a failed trace appears in Jaeger.

    Shows:
    - A span with ERROR status
    - The exception event with stack trace
    - How OTel captures failures in the observability backend
    """
    tracer = tel.tracer

    with tracer.start_as_current_span("demo.deliberate_error") as span:
        span.set_attribute("demo.endpoint", "/debug/error")
        span.set_attribute("demo.error_message", message)

        logger.warning("[OTel Demo] /debug/error: about to raise deliberate exception")

        try:
            # This is intentional — raises so the trace shows ERROR status
            raise ValueError(f"[OTel Demo] Deliberate error: {message}")
        except ValueError as exc:
            # Record on the span so Jaeger shows the exception event + stack trace
            tel.record_error(span, exc)
            # Increment error counter so Grafana error-rate panel shows a spike
            if tel.analysis_errors_counter is not None:
                tel.analysis_errors_counter.add(1, {"endpoint": "/debug/error", "demo": "true"})
            logger.error("[OTel Demo] /debug/error: exception recorded on span", exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
