#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_observability_server.sh
#
# Downloads standalone observability binaries on the SSH server (no Docker).
# Run this ONCE to download everything, then use start_all.sh / stop_all.sh.
#
# Binaries downloaded (from GitHub Releases):
#   otelcol-contrib  v0.113.0
#   jaeger-all-in-one v1.62.0
#   prometheus        v2.55.0
#   loki              v3.2.0
#   grafana           v11.3.0
#
# Usage on the server:
#   bash scripts/setup_observability_server.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BIN_DIR="${HOME}/observability/bin"
DATA_DIR="${HOME}/observability/data"
mkdir -p "$BIN_DIR" "$DATA_DIR/prometheus" "$DATA_DIR/loki" "$DATA_DIR/grafana"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Setting up observability stack (standalone, no Docker) ==="
echo "Bin dir: $BIN_DIR"

# ── Helper: download and extract binary ───────────────────────────────────────
download_bin() {
    local name="$1"
    local url="$2"
    local binary="$3"      # name of binary inside the tarball
    local tarball="/tmp/${name}.tar.gz"

    if [[ -f "$BIN_DIR/$binary" ]]; then
        echo "✅ $name: already present"
        return 0
    fi

    echo "⬇  $name: downloading from $url ..."
    if curl -sL --connect-timeout 30 --max-time 300 "$url" -o "$tarball"; then
        local size
        size=$(wc -c < "$tarball")
        if [[ $size -lt 1000 ]]; then
            echo "❌ $name: download too small ($size bytes) — skipping"
            rm -f "$tarball"
            return 1
        fi
        tar -xzf "$tarball" -C "$BIN_DIR/" "$binary" 2>/dev/null \
            || tar -xzf "$tarball" -C "$BIN_DIR/"
        chmod +x "$BIN_DIR/$binary"
        rm -f "$tarball"
        echo "✅ $name: installed → $BIN_DIR/$binary"
    else
        echo "❌ $name: download failed"
        return 1
    fi
}

# ── 1. OTel Collector Contrib ─────────────────────────────────────────────────
download_bin "otelcol-contrib" \
    "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v0.113.0/otelcol-contrib_0.113.0_linux_amd64.tar.gz" \
    "otelcol-contrib" || true

# ── 2. Jaeger all-in-one ──────────────────────────────────────────────────────
if [[ ! -f "$BIN_DIR/jaeger-all-in-one" ]]; then
    echo "⬇  Jaeger: downloading ..."
    URL="https://github.com/jaegertracing/jaeger/releases/download/v1.62.0/jaeger-2.1.0-linux-amd64.tar.gz"
    # Try v2 first (OTLP native), fall back to v1
    if curl -sL --connect-timeout 30 --max-time 300 "$URL" -o /tmp/jaeger.tar.gz 2>/dev/null && \
       [[ $(wc -c < /tmp/jaeger.tar.gz) -gt 1000 ]]; then
        tar -xzf /tmp/jaeger.tar.gz -C /tmp/ 2>/dev/null
        find /tmp -name "jaeger" -type f 2>/dev/null | head -1 | xargs -I{} cp {} "$BIN_DIR/jaeger-all-in-one" || true
        chmod +x "$BIN_DIR/jaeger-all-in-one" 2>/dev/null || true
        rm -f /tmp/jaeger.tar.gz
        echo "✅ Jaeger v2 installed"
    else
        # Fall back to v1 all-in-one
        URL2="https://github.com/jaegertracing/jaeger/releases/download/v1.62.0/jaeger-1.62.0-linux-amd64.tar.gz"
        if curl -sL --connect-timeout 30 --max-time 300 "$URL2" -o /tmp/jaeger.tar.gz 2>/dev/null && \
           [[ $(wc -c < /tmp/jaeger.tar.gz) -gt 1000 ]]; then
            tar -xzf /tmp/jaeger.tar.gz -C /tmp/ 2>/dev/null
            find /tmp -name "jaeger-all-in-one" -type f 2>/dev/null | head -1 | \
                xargs -I{} cp {} "$BIN_DIR/jaeger-all-in-one" || true
            chmod +x "$BIN_DIR/jaeger-all-in-one" 2>/dev/null || true
            rm -f /tmp/jaeger.tar.gz
            echo "✅ Jaeger v1 installed"
        else
            echo "❌ Jaeger: download failed — check network"
        fi
    fi
else
    echo "✅ Jaeger: already present"
fi

# ── 3. Prometheus ─────────────────────────────────────────────────────────────
if [[ ! -f "$BIN_DIR/prometheus" ]]; then
    echo "⬇  Prometheus: downloading ..."
    URL="https://github.com/prometheus/prometheus/releases/download/v2.55.0/prometheus-2.55.0.linux-amd64.tar.gz"
    if curl -sL --connect-timeout 30 --max-time 300 "$URL" -o /tmp/prom.tar.gz && \
       [[ $(wc -c < /tmp/prom.tar.gz) -gt 1000 ]]; then
        tar -xzf /tmp/prom.tar.gz --strip-components=1 -C "$BIN_DIR/" \
            prometheus-2.55.0.linux-amd64/prometheus \
            prometheus-2.55.0.linux-amd64/promtool 2>/dev/null \
            || tar -xzf /tmp/prom.tar.gz -C /tmp/prom_extract/
        find /tmp -name "prometheus" -type f 2>/dev/null | head -1 | \
            xargs -I{} cp {} "$BIN_DIR/prometheus" 2>/dev/null || true
        chmod +x "$BIN_DIR/prometheus" 2>/dev/null || true
        rm -f /tmp/prom.tar.gz
        echo "✅ Prometheus installed"
    else
        echo "❌ Prometheus: download failed"
    fi
else
    echo "✅ Prometheus: already present"
fi

# ── 4. Loki ───────────────────────────────────────────────────────────────────
if [[ ! -f "$BIN_DIR/loki" ]]; then
    echo "⬇  Loki: downloading ..."
    URL="https://github.com/grafana/loki/releases/download/v3.2.0/loki-linux-amd64.zip"
    if curl -sL --connect-timeout 30 --max-time 300 "$URL" -o /tmp/loki.zip && \
       [[ $(wc -c < /tmp/loki.zip) -gt 1000 ]]; then
        cd /tmp && unzip -q loki.zip loki-linux-amd64 2>/dev/null || true
        cp /tmp/loki-linux-amd64 "$BIN_DIR/loki" 2>/dev/null || true
        chmod +x "$BIN_DIR/loki"
        rm -f /tmp/loki.zip /tmp/loki-linux-amd64
        echo "✅ Loki installed"
    else
        echo "❌ Loki: download failed (grafana CDN blocked?)"
    fi
else
    echo "✅ Loki: already present"
fi

# ── 5. Grafana ────────────────────────────────────────────────────────────────
if [[ ! -f "$BIN_DIR/grafana" ]]; then
    echo "⬇  Grafana: downloading (large, may take 2-3 min) ..."
    URL="https://github.com/grafana/grafana/releases/download/v11.3.0/grafana-11.3.0.linux-amd64.tar.gz"
    if curl -sL --connect-timeout 30 --max-time 600 "$URL" -o /tmp/grafana.tar.gz && \
       [[ $(wc -c < /tmp/grafana.tar.gz) -gt 1000 ]]; then
        mkdir -p "${HOME}/observability/grafana-app"
        tar -xzf /tmp/grafana.tar.gz --strip-components=1 -C "${HOME}/observability/grafana-app/"
        ln -sf "${HOME}/observability/grafana-app/bin/grafana-server" "$BIN_DIR/grafana"
        rm -f /tmp/grafana.tar.gz
        echo "✅ Grafana installed"
    else
        echo "❌ Grafana: download failed"
        echo "   Try: curl -sL https://github.com/grafana/grafana/releases/download/v11.3.0/grafana-11.3.0.linux-amd64.tar.gz -o /tmp/grafana.tar.gz"
    fi
else
    echo "✅ Grafana: already present"
fi

# ── Copy configs into place ───────────────────────────────────────────────────
echo ""
echo "=== Copying configs ==="
cp "$REPO_DIR/observability/otelcol-config-standalone.yaml" \
   "${HOME}/observability/otelcol-config.yaml"
cp "$REPO_DIR/observability/prometheus-standalone.yml" \
   "${HOME}/observability/prometheus.yml"
cp "$REPO_DIR/observability/loki-config.yaml" \
   "${HOME}/observability/loki-config.yaml"
mkdir -p "${HOME}/observability/grafana-provisioning/datasources"
mkdir -p "${HOME}/observability/grafana-provisioning/dashboards"
cp "$REPO_DIR/observability/grafana/provisioning/datasources/datasources.yaml" \
   "${HOME}/observability/grafana-provisioning/datasources/"
cp "$REPO_DIR/observability/grafana/provisioning/dashboards/dashboard-provider.yaml" \
   "${HOME}/observability/grafana-provisioning/dashboards/"
cp "$REPO_DIR/observability/grafana/provisioning/dashboards/village-pond.json" \
   "${HOME}/observability/grafana-provisioning/dashboards/"

echo ""
echo "=== Binary Summary ==="
for b in otelcol-contrib jaeger-all-in-one prometheus loki grafana; do
    if [[ -f "$BIN_DIR/$b" ]]; then
        echo "  ✅ $b"
    else
        echo "  ❌ $b — NOT FOUND (needs manual download)"
    fi
done

echo ""
echo "=== Next step ==="
echo "Run: bash scripts/start_all.sh"
