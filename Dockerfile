# syntax=docker/dockerfile:1.6
#
# Agentic DevOps Extravaganza — container image
#
# This image bundles the static showcase site (index.html + gifs/) AND the
# runtime components needed to reproduce the demos (mock_k8s_server.py +
# zai_proxy.py + robusta_demo.py). It's a single image that serves the site
# and runs the mock K8s API + Z.AI proxy side-by-side.
#
# Build:
#   docker build -t agentic-devops-extravaganza:latest .
#
# Run (showcase site only):
#   docker run -p 8080:80 agentic-devops-extravaganza:latest
#
# Run (full demo stack — site + mock K8s + Z.AI proxy):
#   docker run -p 8080:80 -p 8443:8443 -p 8081:8081 \
#     -e ZAI_CONFIG_PATH=/run/secrets/zai-config \
#     -v $(pwd)/zai-config.json:/run/secrets/zai-config:ro \
#     agentic-devops-extravaganza:latest demo
#
# The image is ~80 MB. Python runtime adds ~25 MB on top of the static assets.

FROM python:3.12-slim AS base

# Don't write .pyc files and don't buffer stdout
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install nginx (for serving the static site) and a few system deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        nginx \
        curl \
        ca-certificates \
        openssl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- copy the static site (index.html + gifs + outputs) ----
COPY index.html /app/site/index.html
COPY gifs/ /app/site/gifs/
COPY outputs/ /app/site/outputs/

# ---- copy the runtime scripts (mock K8s + proxy + robusta demo) ----
COPY scripts/ /app/scripts/
COPY mock-k8s/ /app/mock-k8s/
COPY captured/ /app/captured/
COPY recordings/ /app/recordings/

# Generate a fresh self-signed cert at build time so k8sgpt/kubectl can connect
# over HTTPS even if the user doesn't override the cert.
RUN mkdir -p /app/mock-k8s && \
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout /app/mock-k8s/key.pem \
        -out /app/mock-k8s/cert.pem \
        -days 3650 \
        -subj "/CN=127.0.0.1" \
        -addext "subjectAltName = IP:127.0.0.1,DNS:localhost" 2>/dev/null && \
    chmod 644 /app/mock-k8s/cert.pem /app/mock-k8s/key.pem

# Configure nginx: serve /app/site/ at /
RUN rm -f /etc/nginx/sites-enabled/default && \
    cat > /etc/nginx/conf.d/default.conf <<'NGINX'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    root /app/site;
    index index.html;

    # Gzip for HTML/CSS/JS
    gzip on;
    gzip_types text/plain text/css application/javascript application/json;
    gzip_min_length 1024;

    # Cache static assets aggressively
    location /gifs/ {
        expires 1y;
        add_header Cache-Control "public, max-age=31536000, immutable";
    }
    location /outputs/ {
        expires 1d;
        add_header Cache-Control "public";
    }

    # Security headers
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # SPA-style fallback
    location / {
        try_files $uri $uri/ /index.html;
    }
}
NGINX

# Entrypoint: defaults to "site" (just nginx). Other modes:
#   site  — serve the static site on :80
#   demo  — site + mock K8s API on :8443 + Z.AI proxy on :8081
COPY <<'EOF' /app/entrypoint.sh
#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-site}"
cd /app

case "$MODE" in
  site)
    echo "[entrypoint] Serving static site on :80"
    exec nginx -g "daemon off;"
    ;;

  demo)
    echo "[entrypoint] Starting full demo stack"
    echo "  - nginx (static site)      :80"
    echo "  - mock_k8s_server.py        :8443"
    echo "  - zai_proxy.py              :8081"
    echo ""

    # Start nginx in the background
    nginx -g "daemon off;" &

    # Start the mock K8s API
    ZAI_CONFIG_PATH="${ZAI_CONFIG_PATH:-/run/secrets/zai-config}"
    python scripts/mock_k8s_server.py 8443 &

    # Start the Z.AI proxy if creds are mounted
    if [ -f "$ZAI_CONFIG_PATH" ]; then
        cp "$ZAI_CONFIG_PATH" /etc/.z-ai-config
        python scripts/zai_proxy.py 8081 &
    else
        echo "[entrypoint] WARNING: Z.AI config not found at $ZAI_CONFIG_PATH"
        echo "[entrypoint]          Z.AI proxy will not be started."
        echo "[entrypoint]          Mount your config: -v \$PWD/.z-ai-config:/run/secrets/zai-config:ro"
    fi

    echo ""
    echo "Endpoints:"
    echo "  Showcase site:  http://localhost:8080"
    echo "  Mock K8s API:   https://localhost:8443  (insecure TLS)"
    echo "  Z.AI proxy:     http://localhost:8081"
    echo ""
    echo "Press Ctrl+C to stop."

    # Tail logs forever so the container stays alive
    wait
    ;;

  k8s)
    echo "[entrypoint] Mock K8s API only, on :8443"
    exec python scripts/mock_k8s_server.py 8443
    ;;

  proxy)
    ZAI_CONFIG_PATH="${ZAI_CONFIG_PATH:-/run/secrets/zai-config}"
    if [ -f "$ZAI_CONFIG_PATH" ]; then
        cp "$ZAI_CONFIG_PATH" /etc/.z-ai-config
    fi
    echo "[entrypoint] Z.AI proxy only, on :8081"
    exec python scripts/zai_proxy.py 8081
    ;;

  robusta)
    echo "[entrypoint] Running Robusta autonomous SRE demo (one-shot)"
    ZAI_CONFIG_PATH="${ZAI_CONFIG_PATH:-/run/secrets/zai-config}"
    if [ -f "$ZAI_CONFIG_PATH" ]; then
        cp "$ZAI_CONFIG_PATH" /etc/.z-ai-config"
    fi
    # Start the mock K8s API in the background so robusta_demo.py can query it
    python scripts/mock_k8s_server.py 8443 &
    sleep 1
    exec python scripts/robusta_demo.py
    ;;

  *)
    exec "$@"
    ;;
esac
EOF
RUN chmod +x /app/entrypoint.sh

EXPOSE 80 8443 8081

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["site"]
