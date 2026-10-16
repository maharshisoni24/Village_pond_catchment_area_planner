#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# scripts/stop_all.sh — Gracefully stop all services by PID file / pattern
#
# Usage:  bash scripts/stop_all.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[stop_all]${NC} $*"; }
warn()  { echo -e "${YELLOW}[stop_all]${NC} $*"; }

stop_pid() {
  local name="$1"; local pid_file="$2"
  if [ -f "$pid_file" ]; then
    local PID
    PID=$(cat "$pid_file" 2>/dev/null || echo "")
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
      kill "$PID" && info "Stopped $name (PID $PID)" || warn "Could not kill $PID"
    else
      info "$name: PID file exists but process not running"
    fi
    rm -f "$pid_file"
  else
    info "$name: no PID file"
  fi
}

# Stop in reverse startup order
stop_pid "Frontend"   /tmp/frontend.pid
stop_pid "Backend"    /tmp/backend.pid
stop_pid "Grafana"    /tmp/grafana.pid
stop_pid "Prometheus" /tmp/prometheus.pid
stop_pid "otelcol"    /tmp/otelcol.pid
stop_pid "Loki"       /tmp/loki.pid
stop_pid "Jaeger"     /tmp/jaeger.pid

sleep 2
info "All services stopped."
info "Check with: ps aux | grep -E 'uvicorn|otelcol|jaeger|loki|prometheus|grafana'"
