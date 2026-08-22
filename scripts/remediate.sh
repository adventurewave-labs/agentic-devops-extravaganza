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

# Two things differ between the mock and a real cluster, so they are inputs
# rather than literals:
#   NODE          the mock ships a fixture node called worker-3; kind names its
#                 nodes <cluster>-worker, <cluster>-worker2.
#   STORAGE_CLASS the mock's PVC asks for "standard" (deleted); on kind that
#                 name is already taken by the built-in provisioner, so the
#                 kind fixture asks for "fast-ssd" instead.
NODE="${NODE:-worker-3}"
STORAGE_CLASS="${STORAGE_CLASS:-standard}"

step "1/7  payment-api CrashLoopBackOff -> roll to an image tag that exists"
kn set image deployment/payment-api "api=${FIXED_API_IMAGE:-registry.io/payments/api:1.4.3}"

step "2/7  payment-worker OOMKilled -> raise the memory limit above the working set"
kn patch deployment payment-worker --type=strategic -p \
  '{"spec":{"template":{"spec":{"containers":[{"name":"worker","resources":{"limits":{"memory":"512Mi"},"requests":{"memory":"256Mi"}}}]}}}}'

step "3/7  payment-api-svc has no endpoints -> correct the selector typo"
kn patch service payment-api-svc --type=merge -p \
  '{"spec":{"selector":{"app":"payment-api"}}}'

step "4/7  $NODE DiskPressure -> cordon and drain the node"
if ! k get node "$NODE" >/dev/null 2>&1; then
  echo "  node $NODE is not present on this cluster - skipping"
elif [[ "$(k get node "$NODE" \
      -o jsonpath='{.status.conditions[?(@.type=="DiskPressure")].status}')" != "True" ]]; then
  # On kind the kubelet owns this condition and only reports pressure when the
  # node is genuinely low on disk, so there is usually nothing to remediate.
  echo "  $NODE does not report DiskPressure - nothing to do"
else
  k cordon "$NODE"
  k drain "$NODE" --ignore-daemonsets --delete-emptydir-data --force --timeout=30s \
    2>/dev/null || true
fi

step "5/7  payment-ingress -> create the missing IngressClass"
if k get ingressclass nginx >/dev/null 2>&1; then
  echo "  IngressClass nginx already exists - nothing to do"
else
  # shellcheck disable=SC2086
  k apply $VALIDATE_FLAG -f "$ROOT/manifests/fix-ingressclass.yaml"
fi

step "6/7  payment-ingress -> create the missing backend Service"
if kn get service payment-frontend >/dev/null 2>&1; then
  echo "  Service payment-frontend already exists - nothing to do"
else
  # shellcheck disable=SC2086
  kn create service clusterip payment-frontend --tcp=80:80 --dry-run=client -o yaml \
    | kn apply $VALIDATE_FLAG -f -
fi

step "7/7  payment-data-pvc Pending -> provide StorageClass $STORAGE_CLASS"
if k get storageclass "$STORAGE_CLASS" >/dev/null 2>&1; then
  echo "  StorageClass $STORAGE_CLASS already exists - nothing to do"
else
  # shellcheck disable=SC2086
  sed "s/^  name: standard$/  name: $STORAGE_CLASS/" \
    "$ROOT/manifests/fix-storageclass.yaml" | k apply $VALIDATE_FLAG -f -
fi

printf '\n\033[1;32mRemediation pass complete. Re-run k8sgpt to see the finding count drop.\033[0m\n'
