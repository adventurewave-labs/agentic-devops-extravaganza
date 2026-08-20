# syntax=docker/dockerfile:1.6
#
# Agentic DevOps Extravaganza — container image
#
# This image bundles:
#   - the static showcase site (index.html + gifs/)
#   - mock_k8s_server.py (mock Kubernetes API on :8443)
#   - zai_proxy.py (Z.AI GLM translation proxy on :8081)
#   - robusta_demo.py (Robusta-style autonomous SRE flow)
#   - k8sgpt binary v0.4.36 (real, from GitHub releases)
#   - kubectl binary (real, from dl.k8s.io)
#
# Build:
#   docker build -t agentic-devops-extravaganza:latest .
#
# Run (showcase site only):
#   docker run -p 8080:80 agentic-devops-extravaganza:latest
#
# Run (full demo stack — site + mock K8s + Z.AI proxy + k8sgpt):
#   docker run -p 8080:80 -p 8443:8443 -p 8081:8081 \
#     -v $(pwd)/.z-ai-config:/run/secrets/zai-config:ro \
#     agentic-devops-extravaganza:latest demo
#
# Then inside the container (or from host with kubeconfig):
#   k8sgpt analyze --kubeconfig /app/mock-k8s/kubeconfig.yaml \
#     --kubecontext mock-context -n payment-prod --explain \
#     --backend customrest

FROM python:3.12-slim AS base

# Don't write .pyc files and don't buffer stdout
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# Install system deps: nginx for the static site, curl/openssl/certs for everything else
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        nginx \
        curl \
        ca-certificates \
        openssl \
        jq && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ============================================================
# Install k8sgpt v0.4.36 (real binary from GitHub releases)
# ============================================================
RUN curl -sL https://github.com/k8sgpt-ai/k8sgpt/releases/download/v0.4.36/k8sgpt_Linux_x86_64.tar.gz \
        -o /tmp/k8sgpt.tar.gz && \
    tar xzf /tmp/k8sgpt.tar.gz -C /tmp k8sgpt && \
    mv /tmp/k8sgpt /usr/local/bin/k8sgpt && \
    chmod +x /usr/local/bin/k8sgpt && \
    rm -f /tmp/k8sgpt.tar.gz && \
    k8sgpt version

# ============================================================
# Install kubectl (real binary from dl.k8s.io)
# ============================================================
RUN curl -sL "https://dl.k8s.io/release/v1.30.0/bin/linux/amd64/kubectl" \
        -o /usr/local/bin/kubectl && \
    chmod +x /usr/local/bin/kubectl && \
    kubectl version --client

# ============================================================
# Copy the static site (index.html + gifs + outputs)
# ============================================================
COPY index.html /app/site/index.html
COPY gifs/ /app/site/gifs/
COPY outputs/ /app/site/outputs/

# ============================================================
# Copy the runtime scripts (mock K8s + proxy + robusta demo + UAT)
# ============================================================
COPY scripts/ /app/scripts/
COPY mock-k8s/ /app/mock-k8s/
COPY captured/ /app/captured/
COPY recordings/ /app/recordings/

# ============================================================
# Generate a fresh self-signed cert at build time
# ============================================================
RUN mkdir -p /app/mock-k8s && \
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout /app/mock-k8s/key.pem \
        -out /app/mock-k8s/cert.pem \
        -days 3650 \
        -subj "/CN=127.0.0.1" \
        -addext "subjectAltName = IP:127.0.0.1,DNS:localhost" 2>/dev/null && \
    chmod 644 /app/mock-k8s/cert.pem /app/mock-k8s/key.pem

# ============================================================
# Pre-configure k8sgpt to use the Z.AI proxy via customrest
# This writes ~/.config/k8sgpt/k8sgpt.yaml so k8sgpt is ready
# to call --explain --backend customrest out of the box.
# ============================================================
RUN mkdir -p /root/.config/k8sgpt && \
    printf 'ai:\n    providers:\n        - name: customrest\n          model: glm-4.5\n          password: Z.ai\n          baseurl: http://127.0.0.1:8081\n          temperature: 0.7\n          topp: 0.5\n          topk: 50\n          maxtokens: 2048\n          customheaders: []\n    defaultprovider: ""\ncommit: ""\ndate: ""\nkubeconfig: ""\nkubecontext: ""\nverbose: false\nversion: 0.4.36\n' \
    > /root/.config/k8sgpt/k8sgpt.yaml

# ============================================================
# Configure nginx: serve /app/site/ at /
# ============================================================
RUN rm -f /etc/nginx/sites-enabled/default && \
    printf 'server {\n    listen 80 default_server;\n    server_name _;\n    root /app/site;\n    index index.html;\n    gzip on;\n    gzip_types text/plain text/css application/javascript application/json;\n    gzip_min_length 1024;\n    location /gifs/ { expires 1y; add_header Cache-Control "public, max-age=31536000, immutable"; }\n    location /outputs/ { expires 1d; }\n    add_header X-Content-Type-Options "nosniff" always;\n    add_header X-Frame-Options "DENY" always;\n    add_header Referrer-Policy "strict-origin-when-cross-origin" always;\n    location / { try_files $uri $uri/ /index.html; }\n}\n' \
    > /etc/nginx/conf.d/default.conf

# ============================================================
# Entrypoint script
# ============================================================
COPY <<'ENTRYPOINT' /app/entrypoint.sh
#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-site}"
cd /app
ZAI_CONFIG_PATH="${ZAI_CONFIG_PATH:-/run/secrets/zai-config}"
export KUBECONFIG=/app/mock-k8s/kubeconfig.yaml

# Helper: start mock K8s API in background
start_mock_k8s() {
    echo "[entrypoint] Starting mock K8s API on :8443..."
    python scripts/mock_k8s_server.py 8443 &
    MOCK_PID=$!
    for i in $(seq 1 10); do
        if curl -sk https://127.0.0.1:8443/version >/dev/null 2>&1; then
            echo "[entrypoint] Mock K8s API is ready"
            return 0
        fi
        sleep 0.5
    done
    echo "[entrypoint] ERROR: mock K8s API did not start"
    return 1
}

# Helper: start Z.AI proxy in background (needs config)
start_zai_proxy() {
    if [ -f "$ZAI_CONFIG_PATH" ]; then
        cp "$ZAI_CONFIG_PATH" /etc/.z-ai-config
        echo "[entrypoint] Starting Z.AI proxy on :8081..."
        python scripts/zai_proxy.py 8081 &
        PROXY_PID=$!
        for i in $(seq 1 10); do
            if curl -s http://127.0.0.1:8081/ >/dev/null 2>&1; then
                echo "[entrypoint] Z.AI proxy is ready"
                return 0
            fi
            sleep 0.5
        done
        echo "[entrypoint] ERROR: Z.AI proxy did not start"
        return 1
    else
        echo "[entrypoint] WARNING: Z.AI config not found at $ZAI_CONFIG_PATH"
        echo "[entrypoint]          Z.AI proxy will not start (no LLM calls)."
        echo "[entrypoint]          Mount config: -v \$PWD/.z-ai-config:/run/secrets/zai-config:ro"
        return 0
    fi
}

case "$MODE" in
  site)
    echo "[entrypoint] Serving static site on :80"
    exec nginx -g "daemon off;"
    ;;

  demo)
    echo "[entrypoint] === Full demo stack ==="
    echo ""
    nginx -g "daemon off;" &
    start_mock_k8s
    start_zai_proxy
    echo ""
    echo "Endpoints:"
    echo "  Showcase site:  http://localhost:8080"
    echo "  Mock K8s API:   https://localhost:8443  (insecure TLS)"
    echo "  Z.AI proxy:     http://localhost:8081"
    echo ""
    echo "Run k8sgpt (inside container):"
    echo "  k8sgpt analyze --kubeconfig /app/mock-k8s/kubeconfig.yaml \\"
    echo "    --kubecontext mock-context -n payment-prod --explain \\"
    echo "    --backend customrest"
    echo ""
    echo "Press Ctrl+C to stop."
    wait
    ;;

  k8s)
    echo "[entrypoint] Mock K8s API only, on :8443"
    exec python scripts/mock_k8s_server.py 8443
    ;;

  proxy)
    if [ -f "$ZAI_CONFIG_PATH" ]; then
        cp "$ZAI_CONFIG_PATH" /etc/.z-ai-config
    fi
    echo "[entrypoint] Z.AI proxy only, on :8081"
    exec python scripts/zai_proxy.py 8081
    ;;

  scan)
    echo "[entrypoint] Running k8sgpt analyze (no LLM)..."
    start_mock_k8s
    echo ""
    k8sgpt analyze \
        --kubeconfig /app/mock-k8s/kubeconfig.yaml \
        --kubecontext mock-context \
        --no-cache -n payment-prod
    ;;

  explain)
    echo "[entrypoint] Running k8sgpt analyze --explain (with GLM-4.5)..."
    start_mock_k8s
    start_zai_proxy
    echo ""
    k8sgpt analyze \
        --kubeconfig /app/mock-k8s/kubeconfig.yaml \
        --kubecontext mock-context \
        --no-cache -n payment-prod \
        --explain --backend customrest
    ;;

  robusta)
    echo "[entrypoint] Running Robusta autonomous SRE demo (one-shot)..."
    if [ -f "$ZAI_CONFIG_PATH" ]; then
        cp "$ZAI_CONFIG_PATH" /etc/.z-ai-config
    fi
    start_mock_k8s
    start_zai_proxy
    sleep 1
    exec python scripts/robusta_demo.py
    ;;

  uat)
    echo "[entrypoint] Running 15-test UAT matrix..."
    start_mock_k8s
    start_zai_proxy
    sleep 1
    exec python scripts/run_uat.py
    ;;

  *)
    exec "$@"
    ;;
esac
ENTRYPOINT
RUN chmod +x /app/entrypoint.sh

EXPOSE 80 8443 8081

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["site"]
