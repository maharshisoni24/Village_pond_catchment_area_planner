#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# scripts/start_all.sh  — Start the full observability stack (standalone/no Docker)
#
# Designed for the SSH server at 10.1.75.53 where Docker is not available.
# Binaries live in ~/observability/bin/, configs in ~/observability/configs/.
#
# Usage:  bash scripts/start_all.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

OBS="$HOME/observability"
REPO="$HOME/Village_pond_catchment_area_planner"
LOG_DIR="/tmp"

# ── Colours ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[start_all]${NC} $*"; }
warn()  { echo -e "${YELLOW}[start_all]${NC} $*"; }
error() { echo -e "${RED}[start_all]${NC} $*"; }

die() { error "$*"; exit 1; }

# ── Helper: start a process if not already running ───────────────────────────
start_if_needed() {
  local name="$1"; local pattern="$2"; local cmd="$3"; local log="$4"; local pid_file="$5"
  if ps aux | grep -q "[${pattern:0:1}]${pattern:1}"; then
    info "$name already running"
  else
    info "Starting $name..."
    eval "setsid $cmd > $log 2>&1 &"
    echo $! > "$pid_file"
    disown $!
    info "$name started (PID $(cat $pid_file))"
  fi
}

# ── 1. Jaeger (traces UI + OTLP receiver on non-default ports) ────────────────
start_if_needed "Jaeger" \
  "jaeger-all-in-one" \
  "$OBS/bin/jaeger-all-in-one --collector.otlp.grpc.host-port=:4327 --collector.otlp.http.host-port=:4328" \
  "$LOG_DIR/jaeger.log" \
  "$LOG_DIR/jaeger.pid"

sleep 2

# ── 2. Loki (log storage) ─────────────────────────────────────────────────────
start_if_needed "Loki" \
  "bin/loki" \
  "$OBS/bin/loki -config.file=$OBS/configs/loki-config.yaml" \
  "$LOG_DIR/loki.log" \
  "$LOG_DIR/loki.pid"

# ── 3. OTel Collector ─────────────────────────────────────────────────────────
start_if_needed "otelcol-contrib" \
  "otelcol-contrib" \
  "$OBS/bin/otelcol-contrib --config=$OBS/configs/otelcol-config.yaml" \
  "$LOG_DIR/otelcol.log" \
  "$LOG_DIR/otelcol.pid"

sleep 2

# ── 4. Prometheus ─────────────────────────────────────────────────────────────
start_if_needed "Prometheus" \
  "bin/prometheus" \
  "$OBS/bin/prometheus --config.file=$OBS/configs/prometheus.yml --storage.tsdb.path=$OBS/data/prometheus --web.listen-address=0.0.0.0:9090" \
  "$LOG_DIR/prometheus.log" \
  "$LOG_DIR/prometheus.pid"

# ── 5. Grafana ────────────────────────────────────────────────────────────────
start_if_needed "Grafana" \
  "grafana-server" \
  "$OBS/grafana/bin/grafana-server --homepath=$OBS/grafana cfg:paths.data=$OBS/data/grafana cfg:server.http_port=3000 cfg:security.admin_password=admin cfg:analytics.reporting_enabled=false" \
  "$LOG_DIR/grafana.log" \
  "$LOG_DIR/grafana.pid"

sleep 3

# ── 6. Backend (FastAPI + OTel) ───────────────────────────────────────────────
if ps aux | grep -q "[u]vicorn app.main"; then
  info "Backend already running"
else
  info "Starting backend..."
  cd "$REPO/backend"
  export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
  export OTEL_SERVICE_VERSION=0.2.0
  export OTEL_DEPLOYMENT_ENVIRONMENT=production
  export OTEL_ENABLED=true

  setsid /opt/conda/bin/python -m uvicorn app.main:app \
    --host 0.0.0.0 --port 7000 \
    > "$LOG_DIR/backend.log" 2>&1 &
  echo $! > "$LOG_DIR/backend.pid"
  disown $!
  info "Backend started (PID $(cat $LOG_DIR/backend.pid))"
fi

# ── 7. Frontend (Vite dev) ────────────────────────────────────────────────────
if ps aux | grep -q "[n]pm run dev"; then
  info "Frontend already running"
else
  info "Starting frontend..."
  cd "$REPO/frontend"
  setsid npm run dev -- --host --port 5000 \
    > "$LOG_DIR/frontend.log" 2>&1 &
  echo $! > "$LOG_DIR/frontend.pid"
  disown $!
  info "Frontend started (PID $(cat $LOG_DIR/frontend.pid))"
fi

sleep 5

# ── Health check summary ──────────────────────────────────────────────────────
echo ""
info "══════════════ SERVICE STATUS ══════════════"
check() {
  local name="$1"; local url="$2"
  if curl -sf "$url" > /dev/null 2>&1; then
    echo -e "  ${GREEN}✓${NC} $name"
  else
    echo -e "  ${RED}✗${NC} $name (${url})"
  fi
}
check "Backend     :7000" "http://localhost:7000/"
check "otelcol     :13133" "http://localhost:13133/"
check "Jaeger UI   :16686" "http://localhost:16686/"
check "Loki        :3100"  "http://localhost:3100/ready"
check "Prometheus  :9090"  "http://localhost:9090/-/ready"
check "Grafana     :3000"  "http://localhost:3000/api/health"
check "Frontend    :5000"  "http://localhost:5000/"

echo ""
info "══════════════ SSH TUNNELS ══════════════"
info "Run this on your LAPTOP to access the UIs:"
echo "  ssh -p 2201 -L 16686:localhost:16686 -L 3000:localhost:3000 -L 9090:localhost:9090 -L 4318:localhost:4318 student@10.1.75.53"
echo ""
info "Then open in your browser:"
echo "  Jaeger  → http://localhost:16686"
echo "  Grafana → http://localhost:3000  (admin / admin)"
echo "  Prometheus → http://localhost:9090"
echo ""
info "Done!"
