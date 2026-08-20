#!/usr/bin/env bash
# Agentic DevOps orchestrator.
#
# Usage:
#   ./run.sh site      — serve the static showcase page on :8080 (Python http.server)
#   ./run.sh demo      — start mock K8s API :8443 + Z.AI proxy :8081 + showcase :8080
#   ./run.sh robusta   — run the Robusta autonomous SRE one-shot
#   ./run.sh record    — rebuild the asciinema casts and GIFs
#   ./run.sh status     — show running services
#   ./run.sh stop       — stop everything
#
# Requires Python 3.10+ and (for `record`) the `agg` binary.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOGDIR="$ROOT/var/log"
PIDDIR="$ROOT/var/pids"
mkdir -p "$LOGDIR" "$PIDDIR"

PY="${PYTHON:-python3}"
export KUBECONFIG="${KUBECONFIG:-$ROOT/mock-k8s/kubeconfig.yaml}"

start_svc() {
  local name="$1"; local script="$2"; shift 2
  local pidfile="$PIDDIR/$name.pid"
  local logfile="$LOGDIR/$name.log"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "$name already running (pid $(cat "$pidfile"))"
    return
  fi
  echo "Starting $name..."
  nohup "$PY" "$script" "$@" >"$logfile" 2>&1 &
  echo $! > "$pidfile"
}

stop_svc() {
  local name="$1"
  local pidfile="$PIDDIR/$name.pid"
  if [[ -f "$pidfile" ]]; then
    local pid; pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "Stopping $name (pid $pid)..."
      kill "$pid" 2>/dev/null || true
      sleep 0.5
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
  fi
}

cmd_site() {
  echo "=== Serving showcase site on http://localhost:8080 ==="
  echo "    Press Ctrl+C to stop."
  cd "$ROOT"
  "$PY" -m http.server 8080 --bind 127.0.0.1
}

cmd_demo() {
  echo "=== Agentic DevOps demo stack ==="
  start_svc mock-k8s   "$ROOT/scripts/mock_k8s_server.py" 8443
  sleep 0.5
  start_svc zai-proxy  "$ROOT/scripts/zai_proxy.py" 8081
  sleep 0.5
  start_svc site       "$ROOT/scripts/serve_site.py" 8080 2>/dev/null || true

  # If serve_site.py doesn't exist, just run http.server directly
  if ! kill -0 "$(cat "$PIDDIR/site.pid" 2>/dev/null)" 2>/dev/null; then
    cd "$ROOT"
    nohup "$PY" -m http.server 8080 --bind 127.0.0.1 >"$LOGDIR/site.log" 2>&1 &
    echo $! > "$PIDDIR/site.pid"
  fi

  echo ""
  echo "Endpoints:"
  echo "  Showcase site:  http://localhost:8080"
  echo "  Mock K8s API:   https://localhost:8443  (insecure TLS)"
  echo "  Z.AI proxy:      http://localhost:8081"
  echo ""
  echo "Run k8sgpt:"
  echo "  k8sgpt analyze --kubeconfig $KUBECONFIG --kubecontext mock-context \\"
  echo "    --no-cache -n payment-prod --explain --backend customrest"
  echo ""
  echo "Stop with:  $0 stop"
}

cmd_robusta() {
  echo "=== Running Robusta autonomous SRE demo (one-shot) ==="
  # Ensure mock K8s is up
  if ! kill -0 "$(cat "$PIDDIR/mock-k8s.pid" 2>/dev/null)" 2>/dev/null; then
    start_svc mock-k8s "$ROOT/scripts/mock_k8s_server.py" 8443
    sleep 0.5
  fi
  cd "$ROOT"
  "$PY" scripts/robusta_demo.py
}

cmd_record() {
  echo "=== Rebuilding asciinema casts + GIFs ==="
  cd "$ROOT"
  "$PY" scripts/build_casts.py all
  for name in k8sgpt_scan k8sgpt_explain robusta; do
    echo "  rendering $name.gif..."
    agg "recordings/$name.cast" "gifs/$name.gif" \
      --font-family "JetBrains Mono, DejaVu Sans Mono, monospace" \
      --theme monokai \
      --idle-time-limit 30
  done
  echo ""
  echo "Done. New GIFs are in gifs/"
}

cmd_status() {
  echo "=== Service status ==="
  for svc in mock-k8s zai-proxy site; do
    pidfile="$PIDDIR/$svc.pid"
    if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
      printf "  %-12s RUNNING pid=%s\n" "$svc" "$(cat "$pidfile")"
    else
      printf "  %-12s STOPPED\n" "$svc"
    fi
  done
}

cmd_stop() {
  echo "=== Stopping all services ==="
  for svc in site zai-proxy mock-k8s; do
    stop_svc "$svc"
  done
}

case "${1:-demo}" in
  site)    cmd_site ;;
  demo)    cmd_demo ;;
  robusta) cmd_robusta ;;
  record)  cmd_record ;;
  status)  cmd_status ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; sleep 1; exec "$0" demo ;;
  *)
    echo "Usage: $0 {site|demo|robusta|record|status|stop|restart}"
    exit 1 ;;
esac
