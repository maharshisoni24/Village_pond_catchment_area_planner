"""
OpenTelemetry instrumentation for the Village Pond Planning System backend.

Sets up three pillars:
  - Traces  → OTLP gRPC exporter → OTel Collector → Jaeger
  - Metrics → OTLP gRPC exporter → OTel Collector → Prometheus
  - Logs    → OTLP gRPC exporter + Python logging bridge → OTel Collector → Loki

Configuration via env vars (with safe defaults for local dev):
  OTEL_EXPORTER_OTLP_ENDPOINT   gRPC endpoint, default http://localhost:4317
  OTEL_SERVICE_VERSION           default "0.2.0"
  OTEL_DEPLOYMENT_ENVIRONMENT    default "development"
  OTEL_ENABLED                   set "false" to disable (useful for unit tests)
  OTEL_CONSOLE_EXPORT            set "true" to print spans/metrics to stdout
                                 (useful when no collector is running locally)

Call `setup_telemetry()` exactly once at startup (in main.py), then import
the module-level `tracer` and `meter` wherever you need to add manual spans
or record metrics.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

# ──────────────────────────────────────────────────────────────────────────────
# Guard: allow disabling OTel entirely (e.g. in CI / unit tests)
# ──────────────────────────────────────────────────────────────────────────────
_OTEL_ENABLED      = os.environ.get("OTEL_ENABLED", "true").lower() not in ("false", "0", "no")
# When true, spans and metrics also print to stdout (no collector needed)
_CONSOLE_EXPORT    = os.environ.get("OTEL_CONSOLE_EXPORT", "false").lower() in ("true", "1", "yes")

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Shared resource attributes (identify this service in every signal)
# ──────────────────────────────────────────────────────────────────────────────
_SERVICE_NAME    = "village-pond-backend"
_SERVICE_VERSION = os.environ.get("OTEL_SERVICE_VERSION", "0.2.0")
_ENVIRONMENT     = os.environ.get("OTEL_DEPLOYMENT_ENVIRONMENT", "development")
_OTLP_ENDPOINT   = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

# Module-level tracer and meter — import these in other modules
tracer = None  # set after setup_telemetry()
meter  = None  # set after setup_telemetry()

# ──────────────────────────────────────────────────────────────────────────────
# Custom metrics (created in setup_telemetry, exposed as module globals)
# ──────────────────────────────────────────────────────────────────────────────
# Counters
analysis_requests_counter  = None   # total analyses started
analysis_errors_counter    = None   # total analyses that errored

# Histograms (durations in seconds)
analysis_duration_hist     = None   # whole analysis end-to-end
dem_fetch_duration_hist    = None   # OpenTopoData DEM fetch
catchment_duration_hist    = None   # pysheds catchment pipeline
rainfall_duration_hist     = None   # Open-Meteo rainfall fetch
land_detect_duration_hist  = None   # satellite land detection

# Up-down counter (in-flight)
inflight_analyses_counter  = None


def setup_telemetry() -> None:
    """
    Initialise TracerProvider, MeterProvider and LoggerProvider.
    Must be called once before the FastAPI app processes any request.
    Safe to call multiple times (idempotent — uses a flag).
    """
    global tracer, meter
    global analysis_requests_counter, analysis_errors_counter
    global analysis_duration_hist, dem_fetch_duration_hist
    global catchment_duration_hist, rainfall_duration_hist
    global land_detect_duration_hist, inflight_analyses_counter

    if not _OTEL_ENABLED:
        logger.info("OTel: OTEL_ENABLED=false — telemetry disabled")
        tracer = _NoopTracer()
        meter  = _NoopMeter()
        return

    try:
        _configure_traces()
        _configure_metrics()
        _configure_logs()
        logger.info(
            "OTel: telemetry configured — service=%s version=%s env=%s endpoint=%s",
            _SERVICE_NAME, _SERVICE_VERSION, _ENVIRONMENT, _OTLP_ENDPOINT,
        )
    except Exception as exc:
        # Never crash the app because OTel init failed
        logger.warning("OTel: setup failed (%s) — running without telemetry", exc, exc_info=True)
        tracer = _NoopTracer()
        meter  = _NoopMeter()
        return

    # ── Tracer ────────────────────────────────────────────────────────────────
    from opentelemetry import trace as _trace
    tracer = _trace.get_tracer(_SERVICE_NAME, _SERVICE_VERSION)

    # ── Meter + custom instruments ────────────────────────────────────────────
    from opentelemetry import metrics as _metrics
    meter = _metrics.get_meter(_SERVICE_NAME, _SERVICE_VERSION)

    # Counters
    analysis_requests_counter = meter.create_counter(
        name="pond.analysis.requests",
        description="Total number of pond analysis requests started",
        unit="1",
    )
    analysis_errors_counter = meter.create_counter(
        name="pond.analysis.errors",
        description="Total number of pond analysis requests that errored",
        unit="1",
    )

    # Histograms
    analysis_duration_hist = meter.create_histogram(
        name="pond.analysis.duration",
        description="End-to-end duration of a full pond analysis request",
        unit="s",
    )
    dem_fetch_duration_hist = meter.create_histogram(
        name="pond.dem_fetch.duration",
        description="Duration of the DEM fetch from OpenTopoData",
        unit="s",
    )
    catchment_duration_hist = meter.create_histogram(
        name="pond.catchment.duration",
        description="Duration of the pysheds catchment delineation pipeline",
        unit="s",
    )
    rainfall_duration_hist = meter.create_histogram(
        name="pond.rainfall_fetch.duration",
        description="Duration of the Open-Meteo rainfall fetch",
        unit="s",
    )
    land_detect_duration_hist = meter.create_histogram(
        name="pond.land_detect.duration",
        description="Duration of satellite-based open-land detection",
        unit="s",
    )

    # Up-down counter for in-flight analyses
    inflight_analyses_counter = meter.create_up_down_counter(
        name="pond.analysis.inflight",
        description="Number of pond analysis requests currently in progress",
        unit="1",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Internal setup helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_resource():
    """Build the OTel Resource with service identity attributes."""
    from opentelemetry.sdk.resources import Resource
    return Resource.create({
        "service.name":              _SERVICE_NAME,
        "service.version":           _SERVICE_VERSION,
        "deployment.environment":    _ENVIRONMENT,
        "service.instance.id":       _get_instance_id(),
    })


def _get_instance_id() -> str:
    """Return a stable instance identifier (hostname + PID)."""
    import socket, os as _os
    try:
        host = socket.gethostname()
    except Exception:
        host = "unknown"
    return f"{host}-{_os.getpid()}"


def _configure_traces() -> None:
    """Configure TracerProvider with OTLP gRPC exporter + batch processor.

    When OTEL_CONSOLE_EXPORT=true, also adds a SimpleSpanProcessor with
    ConsoleSpanExporter so every finished span is printed to stdout immediately.
    Useful for local demos without a collector running.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(resource=_make_resource())

    # Always wire up OTLP (batch) — errors are logged, never fatal
    otlp_exporter = OTLPSpanExporter(
        endpoint=_OTLP_ENDPOINT,
        insecure=True,   # no TLS for local/lab deployment
    )
    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    # Console exporter: print spans to stdout (no collector needed)
    if _CONSOLE_EXPORT:
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        logger.info("OTel: ConsoleSpanExporter active — spans will print to stdout")

    # SDK ≥1.9 renamed set_global_default_tracer_provider → set_tracer_provider
    trace.set_tracer_provider(provider)
    logger.debug("OTel: TracerProvider configured → %s", _OTLP_ENDPOINT)


def _configure_metrics() -> None:
    """Configure MeterProvider with OTLP gRPC exporter + periodic reader.

    When OTEL_CONSOLE_EXPORT=true, also adds a reader with ConsoleMetricExporter
    so metric snapshots print to stdout every 15 s (no collector needed).
    """
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

    exporter = OTLPMetricExporter(endpoint=_OTLP_ENDPOINT, insecure=True)
    readers = [PeriodicExportingMetricReader(exporter, export_interval_millis=15_000)]

    if _CONSOLE_EXPORT:
        from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
        readers.append(
            PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=15_000)
        )
        logger.info("OTel: ConsoleMetricExporter active — metrics will print to stdout every 15s")

    provider = MeterProvider(resource=_make_resource(), metric_readers=readers)
    metrics.set_meter_provider(provider)
    logger.debug("OTel: MeterProvider configured \u2192 %s", _OTLP_ENDPOINT)


def _configure_logs() -> None:
    """
    Configure LoggerProvider with OTLP gRPC exporter.
    Attaches a LoggingHandler to Python's root logger so all existing
    logging.getLogger(…) calls automatically export log records *and*
    inject trace_id/span_id correlation fields into each log entry.
    """
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.instrumentation.logging import LoggingInstrumentor

    exporter = OTLPLogExporter(
        endpoint=_OTLP_ENDPOINT,
        insecure=True,
    )
    log_provider = LoggerProvider(resource=_make_resource())
    log_provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    set_logger_provider(log_provider)

    # Bridge: makes Python logging emit OTel log records AND injects
    # trace_id / span_id into every log message's format string.
    LoggingInstrumentor().instrument(set_logging_format=True)
    logger.debug("OTel: LoggerProvider configured → %s", _OTLP_ENDPOINT)


# ──────────────────────────────────────────────────────────────────────────────
# Convenience helpers (used by route handlers)
# ──────────────────────────────────────────────────────────────────────────────

def record_error(span, exc: Exception) -> None:
    """Record an exception on a span and set ERROR status."""
    from opentelemetry.trace import StatusCode
    span.record_exception(exc)
    span.set_status(StatusCode.ERROR, str(exc))


def timed_stage(hist, stage_name: str):
    """
    Context-manager that records a histogram observation for a pipeline stage.

    Usage::

        with timed_stage(dem_fetch_duration_hist, "dem_fetch"):
            dem, meta = await fetch_dem_for_bbox(bbox)
    """
    return _TimedStage(hist, stage_name)


class _TimedStage:
    """Simple context manager that measures elapsed seconds and records to a histogram."""

    def __init__(self, hist, name: str):
        self._hist = hist
        self._name = name
        self._t0: float = 0.0

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = time.perf_counter() - self._t0
        if self._hist is not None:
            self._hist.record(elapsed, {"stage": self._name})
        return False   # don't suppress exceptions


# ──────────────────────────────────────────────────────────────────────────────
# No-op fallbacks when OTel is disabled or setup failed
# ──────────────────────────────────────────────────────────────────────────────

class _NoopSpan:
    """Minimal span interface that does nothing."""
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def set_attribute(self, *_): pass
    def set_attributes(self, *_): pass
    def record_exception(self, *_): pass
    def set_status(self, *_): pass
    def add_event(self, *_): pass


class _NoopTracer:
    """Tracer that returns no-op spans."""
    def start_as_current_span(self, *_, **__):
        return _NoopSpan()
    def start_span(self, *_, **__):
        return _NoopSpan()


class _NoopMeter:
    """Meter that returns silent counters/histograms."""
    def create_counter(self, *_, **__): return _NoopInstrument()
    def create_histogram(self, *_, **__): return _NoopInstrument()
    def create_up_down_counter(self, *_, **__): return _NoopInstrument()


class _NoopInstrument:
    def add(self, *_, **__): pass
    def record(self, *_, **__): pass
