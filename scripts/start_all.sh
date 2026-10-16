#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# start_all.sh — start the Village Pond stack + observability on the SSH server
#
# Usage (on the server):
#   chmod +x scripts/start_all.sh
#   bash scripts/start_all.sh
#
# Services started:
#   OTel Collector   :4317 (gRPC)  :4318 (HTTP)
#   Jaeger UI        :16686
#   Prometheus       :9090
#   Loki             :3100
#   Grafana          :3000
#   Backend FastAPI  :7000 (proxied → :7201 externally)
#   Frontend Vite    :5000 (proxied → :5201 externally)
#
# Logs stored in /tmp/*.log
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OBS_DIR="$REPO_DIR/observability"
LOG_DIR="/tmp"

echo "=== Village Pond OTel Stack — Start ==="
echo "Repo: $REPO_DIR"

# ── Check Docker availability ─────────────────────────────────────────────────
if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
    HAS_DOCKER=1
    echo "✅ Docker available — using docker compose"
else
    HAS_DOCKER=0
    echo "⚠️  Docker not available — using standalone binaries (see FALLBACK section)"
fi

# ── 1. Start observability stack ──────────────────────────────────────────────
if [[ $HAS_DOCKER -eq 1 ]]; then
    cd "$OBS_DIR"
    docker compose up -d
    cd "$REPO_DIR"
    echo "✅ Observability stack started via docker compose"
else
    # Fallback: standalone binaries must be pre-downloaded (see README)
    OBS_BIN="${HOME}/observability/bin"
    if [[ -f "$OBS_BIN/otelcol-contrib" ]]; then
        nohup "$OBS_BIN/otelcol-contrib" --config="$OBS_DIR/otelcol-config.yaml" \
            >"$LOG_DIR/otelcol.log" 2>&1 &
        echo $! >"$LOG_DIR/otelcol.pid"
        echo "✅ OTel Collector started (PID $(cat $LOG_DIR/otelcol.pid))"
    else
        echo "❌ OTel Collector binary not found at $OBS_BIN/otelcol-contrib"
        echo "   Download from https://github.com/open-telemetry/opentelemetry-collector-releases/releases"
    fi

    if [[ -f "$OBS_BIN/jaeger-all-in-one" ]]; then
        nohup "$OBS_BIN/jaeger-all-in-one" \
            --collector.otlp.grpc.host-port=:14250 \
            >"$LOG_DIR/jaeger.log" 2>&1 &
        echo $! >"$LOG_DIR/jaeger.pid"
        echo "✅ Jaeger started (PID $(cat $LOG_DIR/jaeger.pid))"
    fi

    if [[ -f "$OBS_BIN/prometheus" ]]; then
        nohup "$OBS_BIN/prometheus" \
            --config.file="$OBS_DIR/prometheus.yml" \
            --storage.tsdb.path="${HOME}/observability/data/prometheus" \
            >"$LOG_DIR/prometheus.log" 2>&1 &
        echo $! >"$LOG_DIR/prometheus.pid"
        echo "✅ Prometheus started (PID $(cat $LOG_DIR/prometheus.pid))"
    fi

    if [[ -f "$OBS_BIN/loki" ]]; then
        nohup "$OBS_BIN/loki" \
            -config.file="$OBS_DIR/loki-config.yaml" \
            >"$LOG_DIR/loki.log" 2>&1 &
        echo $! >"$LOG_DIR/loki.pid"
        echo "✅ Loki started (PID $(cat $LOG_DIR/loki.pid))"
    fi

    if [[ -f "$OBS_BIN/grafana" ]]; then
        GF_PATHS_CONFIG="${HOME}/observability/grafana.ini"
        nohup "$OBS_BIN/grafana" server \
            --config="$GF_PATHS_CONFIG" \
            cfg:default.paths.home="${HOME}/observability/grafana" \
            >"$LOG_DIR/grafana.log" 2>&1 &
        echo $! >"$LOG_DIR/grafana.pid"
        echo "✅ Grafana started (PID $(cat $LOG_DIR/grafana.pid))"
    fi
fi

# Wait a moment for OTel services to be ready
sleep 3

# ── 2. Start backend (FastAPI / uvicorn) ──────────────────────────────────────
# Kill any existing backend on port 7000
if [[ -f "$LOG_DIR/backend.pid" ]]; then
    OLD_PID=$(cat "$LOG_DIR/backend.pid")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Stopping old backend (PID $OLD_PID)..."
        kill "$OLD_PID" && sleep 2
    fi
fi

cd "$REPO_DIR/backend"
nohup /opt/conda/bin/python -m uvicorn app.main:app \
    --host 0.0.0.0 --port 7000 \
    --env-file .env \
    >"$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
echo $BACKEND_PID >"$LOG_DIR/backend.pid"
echo "✅ Backend started (PID $BACKEND_PID) → http://localhost:7000"
echo "   OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317"

# ── 3. Start frontend (Vite dev) ──────────────────────────────────────────────
if [[ -f "$LOG_DIR/frontend.pid" ]]; then
    OLD_PID=$(cat "$LOG_DIR/frontend.pid")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Stopping old frontend (PID $OLD_PID)..."
        kill "$OLD_PID" && sleep 2
    fi
fi

cd "$REPO_DIR/frontend"
nohup npm run dev -- --host --port 5000 \
    >"$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
echo $FRONTEND_PID >"$LOG_DIR/frontend.pid"
echo "✅ Frontend started (PID $FRONTEND_PID) → http://localhost:5000"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Service Table ==="
printf "%-22s %-10s %-10s %-30s\n" "Service" "Port" "PID" "Log"
printf "%-22s %-10s %-10s %-30s\n" "-------" "----" "---" "---"
printf "%-22s %-10s %-10s %-30s\n" "OTel Collector (gRPC)" "4317" "(docker)" "$LOG_DIR/otelcol.log"
printf "%-22s %-10s %-10s %-30s\n" "OTel Collector (HTTP)" "4318" "(docker)" "$LOG_DIR/otelcol.log"
printf "%-22s %-10s %-10s %-30s\n" "Jaeger UI" "16686" "(docker)" "$LOG_DIR/jaeger.log"
printf "%-22s %-10s %-10s %-30s\n" "Prometheus" "9090" "(docker)" "$LOG_DIR/prometheus.log"
printf "%-22s %-10s %-10s %-30s\n" "Loki" "3100" "(docker)" "$LOG_DIR/loki.log"
printf "%-22s %-10s %-10s %-30s\n" "Grafana" "3000" "(docker)" "$LOG_DIR/grafana.log"
printf "%-22s %-10s %-10s %-30s\n" "Backend (FastAPI)" "7000 (→7201)" "$BACKEND_PID" "$LOG_DIR/backend.log"
printf "%-22s %-10s %-10s %-30s\n" "Frontend (Vite)" "5000 (→5201)" "$FRONTEND_PID" "$LOG_DIR/frontend.log"

echo ""
echo "=== SSH port-forward commands (run on your LAPTOP) ==="
echo "  Jaeger:     ssh -p 2201 -L 16686:localhost:16686 student@10.1.75.53"
echo "  Grafana:    ssh -p 2201 -L 3000:localhost:3000 student@10.1.75.53"
echo "  Prometheus: ssh -p 2201 -L 9090:localhost:9090 student@10.1.75.53"
echo "  (combine):  ssh -p 2201 -L 16686:localhost:16686 -L 3000:localhost:3000 -L 9090:localhost:9090 student@10.1.75.53"
echo ""
echo "=== OTel endpoints ==="
echo "  Collector gRPC (backend):   http://localhost:4317"
echo "  Collector HTTP (browser):   http://localhost:4318"
echo ""
echo "Done. Wait ~10s for backend to be ready, then:"
echo "  curl http://localhost:7000/    # should return {status: ok}"
