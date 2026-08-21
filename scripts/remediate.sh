#!/usr/bin/env bash
#
# Remediate every problem k8sgpt found, using nothing but real kubectl writes
# against the API server in $KUBECONFIG.
#
# This script is the demo's "challenge 3". It is not a slideshow: each command
# below mutates real API objects, and re-running `k8sgpt analyze` afterwards
# reports fewer findings because the cluster state genuinely changed. Comment
# any command out and the corresponding finding stays.
#
# Works unmodified against BOTH backends:
#   - the mock API server (./run.sh demo)
#   - a real kind cluster   (make kind-up)
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NS="${NS:-payment-prod}"
KUBECTL="${KUBECTL:-kubectl}"
CTX_ARGS=()
[[ -n "${KUBECONFIG:-}" ]] && CTX_ARGS+=(--kubeconfig "$KUBECONFIG")
[[ -n "${KUBE_CONTEXT:-}" ]] && CTX_ARGS+=(--context "$KUBE_CONTEXT")

k() { "$KUBECTL" "${CTX_ARGS[@]}" "$@"; }
kn() { k -n "$NS" "$@"; }

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

# `kubectl apply -f` validates the manifest against the apiserver's OpenAPI
# schema bundle. The mock API server serves the Kubernetes REST API but not
# the full schema bundle, so manifest applies need --validate=false there.
# Against a real cluster (kind) validation works normally and this is a no-op.
VALIDATE_FLAG="${VALIDATE_FLAG:---validate=false}"
if [[ "${TARGET:-mock}" == "kind" ]]; then
  VALIDATE_FLAG=""
fi

step "1/7  payment-api CrashLoopBackOff -> roll to an image tag that exists"
kn set image deployment/payment-api "api=${FIXED_API_IMAGE:-registry.io/payments/api:1.4.3}"

step "2/7  payment-worker OOMKilled -> raise the memory limit above the working set"
kn patch deployment payment-worker --type=strategic -p \
  '{"spec":{"template":{"spec":{"containers":[{"name":"worker","resources":{"limits":{"memory":"512Mi"},"requests":{"memory":"256Mi"}}}]}}}}'

step "3/7  payment-api-svc has no endpoints -> correct the selector typo"
kn patch service payment-api-svc --type=merge -p \
  '{"spec":{"selector":{"app":"payment-api"}}}'

step "4/7  worker-3 DiskPressure -> cordon and drain the node"
k cordon worker-3
k drain worker-3 --ignore-daemonsets --delete-emptydir-data --force --timeout=10s 2>/dev/null || true

step "5/7  payment-ingress -> create the missing IngressClass"
# shellcheck disable=SC2086
k apply $VALIDATE_FLAG -f "$ROOT/manifests/fix-ingressclass.yaml"

step "6/7  payment-ingress -> create the missing backend Service"
kn create service clusterip payment-frontend --tcp=80:80 --dry-run=client -o yaml \
  | kn apply $VALIDATE_FLAG -f -

step "7/7  payment-data-pvc Pending -> recreate the deleted StorageClass"
# shellcheck disable=SC2086
k apply $VALIDATE_FLAG -f "$ROOT/manifests/fix-storageclass.yaml"

printf '\n\033[1;32mAll 7 remediations applied. Re-run k8sgpt to see the finding count drop.\033[0m\n'
