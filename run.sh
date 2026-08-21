#!/usr/bin/env bash
#
# Agentic DevOps Extravaganza — one entrypoint for the mock-backed demo.
#
#   ./run.sh site        serve the showcase page            (:8080)
#   ./run.sh demo        site + mock K8s API + LLM proxy    (:8080/:8443/:8081)
#   ./run.sh scan        k8sgpt analyze, no LLM
#   ./run.sh explain     k8sgpt analyze --explain
#   ./run.sh remediate   apply the real kubectl fixes, then re-scan
#   ./run.sh triage      the alert -> context -> LLM -> Slack-card agent
#   ./run.sh uat         the acceptance suite
#   ./run.sh reset       restore the pristine broken cluster
#   ./run.sh record      rebuild the asciinema casts and GIFs
#   ./run.sh status      what is running
#   ./run.sh stop        stop everything
#
# For the real-cluster path (real Robusta on kind) see the Makefile.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGDIR="$ROOT/var/log"
PIDDIR="$ROOT/var/pids"
mkdir -p "$LOGDIR" "$PIDDIR"

PY="${PYTHON:-python3}"
export KUBECONFIG="${KUBECONFIG:-$ROOT/mock-k8s/kubeconfig.yaml}"
export KUBE_CONTEXT="${KUBE_CONTEXT:-mock-context}"
export LLM_BACKEND="${LLM_BACKEND:-ollama}"

MOCK_URL="https://127.0.0.1:${AOPS_MOCK_K8S_PORT:-8443}"
K8SGPT="${K8SGPT:-k8sgpt}"

have() { command -v "$1" >/dev/null 2>&1; }

need_k8sgpt() {
  if ! have "$K8SGPT"; then
    cat >&2 <<'MSG'
k8sgpt is not on PATH.

  Linux:  curl -sL https://github.com/k8sgpt-ai/k8sgpt/releases/download/v0.4.36/k8sgpt_Linux_x86_64.tar.gz \
            | tar xz -C /usr/local/bin k8sgpt
  macOS:  brew install k8sgpt

Or use the container, which bundles it:  docker compose run --rm scan
MSG
    exit 1
  fi
}

start_svc() {
  local name="$1" script="$2"; shift 2
  local pidfile="$PIDDIR/$name.pid" logfile="$LOGDIR/$name.log"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "  $name already running (pid $(cat "$pidfile"))"
    return
  fi
  nohup "$PY" "$script" "$@" >"$logfile" 2>&1 &
  echo $! > "$pidfile"
  echo "  started $name (pid $(cat "$pidfile"), log $logfile)"
}

stop_svc() {
  local name="$1" pidfile="$PIDDIR/$1.pid"
  [[ -f "$pidfile" ]] || return 0
  local pid; pid="$(cat "$pidfile")"
  if kill -0 "$pid" 2>/dev/null; then
    echo "  stopping $name (pid $pid)"
    kill "$pid" 2>/dev/null || true
    sleep 0.5
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
}

wait_for_mock() {
  for _ in $(seq 1 30); do
    curl -sk "$MOCK_URL/_demo/health" >/dev/null 2>&1 && return 0
    sleep 0.3
  done
  echo "mock K8s API did not come up; see $LOGDIR/mock-k8s.log" >&2
  return 1
}

ensure_stack() {
  # Don't start a second server on a port that already answers — that used to
  # leave a crash-looping process writing "Address already in use" forever.
  if curl -sk "$MOCK_URL/_demo/health" >/dev/null 2>&1; then
    echo "  mock-k8s already listening on $MOCK_URL"
  else
    start_svc mock-k8s "$ROOT/scripts/mock_k8s_server.py" "${AOPS_MOCK_K8S_PORT:-8443}"
    wait_for_mock
  fi
  if [[ "${1:-}" == "with-llm" ]]; then
    if curl -s "http://127.0.0.1:${AOPS_LLM_PROXY_PORT:-8081}/" >/dev/null 2>&1; then
      echo "  llm-proxy already listening on :${AOPS_LLM_PROXY_PORT:-8081}"
    else
      start_svc llm-proxy "$ROOT/scripts/llm_proxy.py" "${AOPS_LLM_PROXY_PORT:-8081}"
      sleep 0.5
    fi
  fi
}

cmd_site() {
  exec "$PY" "$ROOT/scripts/serve_site.py" "${AOPS_SITE_PORT:-8080}"
}

cmd_demo() {
  echo "=== Agentic DevOps demo stack ==="
  ensure_stack with-llm
  start_svc site "$ROOT/scripts/serve_site.py" "${AOPS_SITE_PORT:-8080}"
  cat <<MSG

Endpoints
  showcase site   http://localhost:${AOPS_SITE_PORT:-8080}
  mock K8s API    $MOCK_URL           (self-signed TLS)
  LLM proxy       http://localhost:${AOPS_LLM_PROXY_PORT:-8081}   (backend: $LLM_BACKEND)

Cluster state
$(curl -sk "$MOCK_URL/_demo/health" | "$PY" -m json.tool | sed 's/^/  /')

Next
  ./run.sh scan        see the findings
  ./run.sh remediate   fix them for real, then re-scan
  ./run.sh stop        shut down
MSG
}

cmd_scan() {
  need_k8sgpt
  ensure_stack
  "$K8SGPT" analyze --kubeconfig "$KUBECONFIG" --kubecontext "$KUBE_CONTEXT" \
    --no-cache -n payment-prod
}

cmd_explain() {
  need_k8sgpt
  ensure_stack with-llm
  echo "LLM backend: $LLM_BACKEND ($(curl -s "http://127.0.0.1:${AOPS_LLM_PROXY_PORT:-8081}/" | "$PY" -c 'import json,sys;d=json.load(sys.stdin);print(d["model"], "via", d["base_url"] or "cache")' 2>/dev/null || echo unknown))"
  "$K8SGPT" analyze --kubeconfig "$KUBECONFIG" --kubecontext "$KUBE_CONTEXT" \
    --no-cache -n payment-prod --explain --backend customrest
}

cmd_remediate() {
  need_k8sgpt
  ensure_stack
  echo "=== BEFORE ==="
  local before
  before="$("$K8SGPT" analyze --kubeconfig "$KUBECONFIG" --kubecontext "$KUBE_CONTEXT" \
    --no-cache -n payment-prod 2>/dev/null | grep -cE '^[0-9]+: ' || true)"
  echo "  k8sgpt findings: $before"
  "$ROOT/scripts/remediate.sh"
  echo
  echo "=== AFTER ==="
  local after
  after="$("$K8SGPT" analyze --kubeconfig "$KUBECONFIG" --kubecontext "$KUBE_CONTEXT" \
    --no-cache -n payment-prod 2>/dev/null | grep -cE '^[0-9]+: ' || true)"
  echo "  k8sgpt findings: $after"
  echo
  echo "  $before -> $after, from real kubectl writes against the API."
  echo "  ./run.sh reset  puts the cluster back."
}

cmd_triage() {
  ensure_stack with-llm
  "$PY" "$ROOT/scripts/alert_triage_agent.py" "$@"
}

cmd_uat() {
  ensure_stack with-llm
  "$PY" "$ROOT/scripts/run_uat.py" "$@"
}

cmd_reset() {
  curl -sk -X POST "$MOCK_URL/_demo/reset" | "$PY" -m json.tool
}

cmd_record() {
  have agg || { echo "agg not found: https://github.com/asciinema/agg/releases" >&2; exit 1; }
  "$PY" "$ROOT/scripts/record_demos.py" "$@"
}

cmd_status() {
  for svc in mock-k8s llm-proxy site; do
    local pidfile="$PIDDIR/$svc.pid"
    if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
      printf "  %-11s RUNNING pid=%s\n" "$svc" "$(cat "$pidfile")"
    else
      printf "  %-11s STOPPED\n" "$svc"
    fi
  done
}

cmd_stop() {
  for svc in site llm-proxy mock-k8s; do stop_svc "$svc"; done
}

case "${1:-demo}" in
  site)      shift; cmd_site "$@" ;;
  demo)      shift; cmd_demo "$@" ;;
  scan)      shift; cmd_scan "$@" ;;
  explain)   shift; cmd_explain "$@" ;;
  remediate) shift; cmd_remediate "$@" ;;
  triage|robusta) shift; cmd_triage "$@" ;;
  uat)       shift; cmd_uat "$@" ;;
  reset)     shift; cmd_reset "$@" ;;
  record)    shift; cmd_record "$@" ;;
  status)    shift; cmd_status "$@" ;;
  stop)      shift; cmd_stop "$@" ;;
  restart)   cmd_stop; sleep 1; exec "$0" demo ;;
  *) echo "Usage: $0 {site|demo|scan|explain|remediate|triage|uat|reset|record|status|stop}" >&2; exit 1 ;;
esac
