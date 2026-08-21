# syntax=docker/dockerfile:1.6
#
# Bundles the real k8sgpt and kubectl binaries with the mock Kubernetes API
# server, the LLM proxy, the alert triage agent and the showcase site.
#
#   docker compose run --rm scan        k8sgpt analyze, no LLM
#   docker compose run --rm remediate   real kubectl fixes, 8 findings -> 0
#   docker compose run --rm uat         the acceptance suite
#   docker compose up demo              full stack

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    AOPS_ROOT=/app \
    KUBECONFIG=/app/mock-k8s/kubeconfig.yaml \
    KUBE_CONTEXT=mock-context

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        nginx curl ca-certificates openssl jq fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

# Pillow powers scripts/cast_to_gif.py so `run.sh record` needs no extra binary.
RUN pip install --no-cache-dir "pillow>=10,<13"

WORKDIR /app

ARG K8SGPT_VERSION=v0.4.36
ARG KUBECTL_VERSION=v1.30.0

RUN curl -fsSL "https://github.com/k8sgpt-ai/k8sgpt/releases/download/${K8SGPT_VERSION}/k8sgpt_Linux_x86_64.tar.gz" \
        -o /tmp/k8sgpt.tar.gz && \
    tar xzf /tmp/k8sgpt.tar.gz -C /tmp k8sgpt && \
    mv /tmp/k8sgpt /usr/local/bin/k8sgpt && \
    chmod +x /usr/local/bin/k8sgpt && \
    rm -f /tmp/k8sgpt.tar.gz && \
    k8sgpt version

RUN curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
        -o /usr/local/bin/kubectl && \
    chmod +x /usr/local/bin/kubectl && \
    kubectl version --client

COPY . /app

# k8sgpt's customrest backend points at the in-container LLM proxy. Which model
# actually answers is decided at runtime by $LLM_BACKEND — no vendor is baked in.
RUN mkdir -p /root/.config/k8sgpt && \
    printf 'ai:\n    providers:\n        - name: customrest\n          model: llama3.1\n          password: none\n          baseurl: http://127.0.0.1:8081\n          temperature: 0.7\n          topp: 0.5\n          topk: 50\n          maxtokens: 2048\n          customheaders: []\n    defaultprovider: ""\nkubeconfig: ""\nkubecontext: ""\nverbose: false\nversion: 0.4.36\n' \
    > /root/.config/k8sgpt/k8sgpt.yaml

RUN rm -f /etc/nginx/sites-enabled/default && \
    printf 'server {\n    listen 80 default_server;\n    server_name _;\n    root /app;\n    index index.html;\n    gzip on;\n    gzip_types text/plain text/css application/javascript application/json;\n    location /gifs/ { expires 1y; add_header Cache-Control "public, max-age=31536000, immutable"; }\n    add_header X-Content-Type-Options "nosniff" always;\n    add_header X-Frame-Options "DENY" always;\n    location / { try_files $uri $uri/ /index.html; }\n}\n' \
    > /etc/nginx/conf.d/default.conf

COPY <<'ENTRYPOINT' /app/entrypoint.sh
#!/usr/bin/env bash
set -euo pipefail
cd /app
MODE="${1:-site}"; shift || true

start_mock() {
  python scripts/mock_k8s_server.py 8443 &
  for _ in $(seq 1 40); do
    curl -sk https://127.0.0.1:8443/_demo/health >/dev/null 2>&1 && return 0
    sleep 0.25
  done
  echo "[entrypoint] mock K8s API did not start" >&2; return 1
}

start_proxy() {
  python scripts/llm_proxy.py 8081 &
  for _ in $(seq 1 40); do
    curl -s http://127.0.0.1:8081/ >/dev/null 2>&1 && return 0
    sleep 0.25
  done
  echo "[entrypoint] LLM proxy did not start" >&2; return 1
}

case "$MODE" in
  site)      exec nginx -g "daemon off;" ;;
  k8s)       exec python scripts/mock_k8s_server.py 8443 ;;
  proxy)     exec python scripts/llm_proxy.py 8081 ;;
  demo)
    nginx -g "daemon off;" &
    start_mock; start_proxy
    echo; echo "site :80   mock K8s :8443   LLM proxy :8081 (backend ${LLM_BACKEND:-ollama})"
    curl -sk https://127.0.0.1:8443/_demo/health | python -m json.tool
    wait ;;
  scan)      start_mock; exec k8sgpt analyze --kubecontext mock-context --no-cache -n payment-prod ;;
  explain)   start_mock; start_proxy; exec k8sgpt analyze --kubecontext mock-context --no-cache -n payment-prod --explain --backend customrest ;;
  remediate) start_mock; exec ./run.sh remediate ;;
  triage)    start_mock; start_proxy; exec python scripts/alert_triage_agent.py "$@" ;;
  uat)       start_mock; start_proxy; exec python scripts/run_uat.py "$@" ;;
  *)         exec "$MODE" "$@" ;;
esac
ENTRYPOINT
RUN chmod +x /app/entrypoint.sh /app/run.sh /app/scripts/remediate.sh

EXPOSE 80 8443 8081
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["site"]
