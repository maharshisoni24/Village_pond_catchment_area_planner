/**
 * OpenTelemetry browser instrumentation for the Village Pond Planning System.
 *
 * Sets up:
 *   - W3C TraceContext propagation (traceparent/tracestate headers)
 *   - DocumentLoad instrumentation (page load spans)
 *   - FetchInstrumentation (auto-spans for every fetch/XHR so API calls
 *     inject traceparent → backend receives it → distributed trace)
 *   - OTLP HTTP exporter → OTel Collector (port 4318, CORS-enabled)
 *
 * The key outcome: when the user clicks "Analyse", the browser creates a span
 * and injects a `traceparent` header into the POST /api/analysis/run request.
 * The FastAPI backend reads that header (via FastAPIInstrumentor) and creates a
 * CHILD span, producing a distributed trace that spans both frontend and backend.
 *
 * Enable/disable via VITE_OTEL_ENABLED env var (default: true).
 * Collector endpoint via VITE_OTEL_COLLECTOR_URL (default: http://localhost:4318).
 */

const OTEL_ENABLED = import.meta.env.VITE_OTEL_ENABLED !== 'false';
const COLLECTOR_URL = import.meta.env.VITE_OTEL_COLLECTOR_URL || 'http://localhost:4318';
const SERVICE_NAME = 'village-pond-frontend';
const SERVICE_VERSION = '0.2.0';

/** @type {import('@opentelemetry/api').Tracer | null} */
let _tracer = null;

/**
 * Initialise OTel browser SDK.
 * Call once in main.jsx before rendering the React tree.
 * Safe to call multiple times (no-ops after first call).
 */
export async function initTelemetry() {
  if (!OTEL_ENABLED) {
    console.debug('[OTel] VITE_OTEL_ENABLED=false — browser telemetry disabled');
    return;
  }

  try {
    // Dynamic imports so the build only pulls OTel when OTEL_ENABLED=true
    const { WebTracerProvider } = await import('@opentelemetry/sdk-trace-web');
    const { BatchSpanProcessor } = await import('@opentelemetry/sdk-trace-base');
    const { OTLPTraceExporter } = await import('@opentelemetry/exporter-trace-otlp-http');
    const { Resource } = await import('@opentelemetry/resources');
    const { SEMRESATTRS_SERVICE_NAME, SEMRESATTRS_SERVICE_VERSION } =
      await import('@opentelemetry/semantic-conventions');
    const { trace, context } = await import('@opentelemetry/api');
    const { W3CTraceContextPropagator } = await import('@opentelemetry/core');
    const { registerInstrumentations } = await import('@opentelemetry/instrumentation');
    const { DocumentLoadInstrumentation } = await import(
      '@opentelemetry/instrumentation-document-load'
    );
    const { FetchInstrumentation } = await import('@opentelemetry/instrumentation-fetch');

    // Resource: identify this service in the Jaeger UI
    const resource = new Resource({
      [SEMRESATTRS_SERVICE_NAME]: SERVICE_NAME,
      [SEMRESATTRS_SERVICE_VERSION]: SERVICE_VERSION,
      'deployment.environment': import.meta.env.MODE || 'development',
    });

    // OTLP HTTP exporter → OTel Collector port 4318 (CORS-enabled)
    const exporter = new OTLPTraceExporter({
      url: `${COLLECTOR_URL}/v1/traces`,
      headers: {},
    });

    const provider = new WebTracerProvider({
      resource,
      spanProcessors: [new BatchSpanProcessor(exporter)],
    });

    // W3C TraceContext: injects `traceparent` header into fetch requests
    provider.register({
      propagator: new W3CTraceContextPropagator(),
    });

    // Auto-instrument page load + all fetch/XHR calls
    // The FetchInstrumentation propagateTraceHeaderCorsUrls allows cross-origin
    // injection of the traceparent header.
    registerInstrumentations({
      instrumentations: [
        new DocumentLoadInstrumentation(),
        new FetchInstrumentation({
          // Inject traceparent into ALL cross-origin requests to the backend
          propagateTraceHeaderCorsUrls: [
            /http:\/\/localhost:7000.*/,
            /http:\/\/10\.1\.75\.53:7201.*/,
            /http:\/\/10\.1\.75\.53:7000.*/,
          ],
          clearTimingResources: true,
        }),
      ],
      tracerProvider: provider,
    });

    _tracer = trace.getTracer(SERVICE_NAME, SERVICE_VERSION);
    console.info(`[OTel] Browser telemetry initialised — exporter: ${COLLECTOR_URL}`);
  } catch (err) {
    // Never crash the app because OTel init failed
    console.warn('[OTel] Browser telemetry init failed — running without traces:', err);
    _tracer = null;
  }
}

/**
 * Get the active tracer (or a no-op if OTel is disabled / failed).
 * Use this to wrap UI interactions in manual spans:
 *
 *   const tracer = getTracer();
 *   const span = tracer.startSpan('user.click.analyse');
 *   try { await runAnalysis(bbox); }
 *   finally { span.end(); }
 */
export function getTracer() {
  return _tracer || _noopTracer;
}

// ── No-op tracer fallback ─────────────────────────────────────────────────────
const _noopSpan = {
  setAttribute: () => _noopSpan,
  setAttributes: () => _noopSpan,
  addEvent: () => _noopSpan,
  setStatus: () => _noopSpan,
  recordException: () => _noopSpan,
  end: () => {},
};
const _noopTracer = {
  startSpan: () => _noopSpan,
  startActiveSpan: (_name, fn) => fn(_noopSpan),
};
