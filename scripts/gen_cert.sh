#!/usr/bin/env bash
# Generate a self-signed cert for the mock k8s API server
set -e
mkdir -p /home/z/my-project/mock-k8s
cd /home/z/my-project/mock-k8s

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout key.pem -out cert.pem -days 3650 \
  -subj "/CN=127.0.0.1" \
  -addext "subjectAltName = IP:127.0.0.1,DNS:localhost" 2>&1 | tail -3

ls -la cert.pem key.pem
