#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# stop_all.sh — gracefully stop all Village Pond services
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OBS_DIR="$REPO_DIR/observability"
LOG_DIR="/tmp"

echo "=== Village Pond OTel Stack — Stop ==="

# ── Stop backend ──────────────────────────────────────────────────────────────
if [[ -f "$LOG_DIR/backend.pid" ]]; then
    PID=$(cat "$LOG_DIR/backend.pid")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping backend (PID $PID)..."
        kill "$PID"
    else
        echo "Backend (PID $PID) already stopped"
    fi
    rm -f "$LOG_DIR/backend.pid"
fi

# ── Stop frontend ─────────────────────────────────────────────────────────────
if [[ -f "$LOG_DIR/frontend.pid" ]]; then
    PID=$(cat "$LOG_DIR/frontend.pid")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping frontend (PID $PID)..."
        kill "$PID"
    else
        echo "Frontend (PID $PID) already stopped"
    fi
    rm -f "$LOG_DIR/frontend.pid"
fi

# ── Stop observability stack ──────────────────────────────────────────────────
if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
    cd "$OBS_DIR"
    docker compose down
    cd "$REPO_DIR"
    echo "✅ Observability stack stopped via docker compose"
else
    for svc in otelcol jaeger prometheus loki grafana; do
        pidfile="$LOG_DIR/${svc}.pid"
        if [[ -f "$pidfile" ]]; then
            PID=$(cat "$pidfile")
            if kill -0 "$PID" 2>/dev/null; then
                echo "Stopping $svc (PID $PID)..."
                kill "$PID"
            fi
            rm -f "$pidfile"
        fi
    done
fi

echo "Done."
