#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# run_local_demo.sh — Start Village Pond backend with OTel console
# export enabled. Spans and metrics print to this terminal window.
#
# Usage:  bash scripts/run_local_demo.sh
# Stop:   Ctrl-C
# ─────────────────────────────────────────────────────────────────
set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$REPO_ROOT/backend/venv/bin/activate"

if [ ! -f "$VENV" ]; then
    echo "ERROR: venv not found at $VENV"
    echo "  Run: cd backend && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Village Pond — OTel CONSOLE DEMO                           ║"
echo "║  Spans  → printed here as JSON when each request finishes   ║"
echo "║  Metrics→ printed here every 15 s                           ║"
echo "║  Backend→ http://localhost:7000                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

source "$VENV"

cd "$REPO_ROOT/backend"

exec env \
    OTEL_ENABLED=true \
    OTEL_CONSOLE_EXPORT=true \
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
    OTEL_SERVICE_VERSION=0.2.0 \
    OTEL_DEPLOYMENT_ENVIRONMENT=development \
    python3 -m uvicorn app.main:app --host 0.0.0.0 --port 7000
