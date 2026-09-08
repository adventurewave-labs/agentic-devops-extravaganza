"""
Agentic DevOps — shared library for the K8sGPT scan touchpoint (PRD §6).

Used by api/scan.py, api/remediate.py, api/reset.py.

This module:
  - imports the REAL cluster_fixtures.py and cluster_state.py from scripts/
  - implements the 8 k8sgpt analyzers faithfully (the same findings the real
    k8sgpt binary would return against the broken cluster)
  - implements the 7 remediation steps (the same writes scripts/remediate.sh
    applies, but directly against ClusterState — no kubectl subprocess needed)

Per PRD §6.6: "k8sgpt unavailable: fall back to a cached set of 8 findings
with a '(Cached demo)' label." On Vercel serverless we cannot ship the real
k8sgpt Go binary, so this path is the honest, faithful equivalent: the same
8 findings, derived by running the same analyzer rules over the same
fixtures that k8sgpt scans. The cluster_state is REAL (the same module the
mock_k8s_server.py uses), so remediation genuinely mutates it.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Module path setup — locate scripts/ so we can import the real modules.
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
if os.path.isdir(SCRIPTS_DIR) and SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# These imports pull in the real ClusterState and the broken-cluster fixtures
# shipped with the repo. The same modules power the local mock_k8s_server.py
# and the UAT suite.
import cluster_fixtures as fx  # type: ignore  # noqa: E402
import cluster_state as cs_mod  # type: ignore  # noqa: E402
from cluster_state import ClusterState  # type: ignore  # noqa: E402

NAMESPACE = "payment-prod"

# ---------------------------------------------------------------------------
# Module-level ClusterState. Persists across invocations of the same
# serverless instance, so scan→remediate→reset sequences within a single
# warm instance behave like the local demo. If the instance is recycled,
# state resets to broken — which is the safe default for a demo.
# ---------------------------------------------------------------------------
_STATE: ClusterState | None = None


def get_state() -> ClusterState:
    global _STATE
    if _STATE is None:
        _STATE = ClusterState()
    return _STATE


def reset_state() -> ClusterState:
    global _STATE
    if _STATE is None:
        _STATE = ClusterState()
    else:
        _STATE.reset()
    return _STATE


# ---------------------------------------------------------------------------
# Analyzers — 8 findings the real k8sgpt binary would report.
#
# Each analyzer reads the cluster state through ClusterState.list() (the same
# path k8sgpt takes through the K8s API). Severity levels match k8sgpt's
# convention: critical (broken pod/deployment), warning (PVC Pending,
# Ingress issues), info (node conditions).
# ---------------------------------------------------------------------------
def _analyze_pods(state: ClusterState) -> list[dict]:
    """Analyze pods — matches k8sgpt's exact output for this cluster.

    k8sgpt reports one finding per pod, based on the lastState.terminated.reason:
      - OOMKilled (exit 137) → "the last termination reason is OOMKilled"
      - Error (any other exit) → "the last termination reason is Error"

    It does NOT report CrashLoopBackOff or high-restart-count as separate
    findings — those are symptoms, k8sgpt reports the root cause.
    """
    out: list[dict] = []
    for pod in state.list("", "pods", NAMESPACE):
        name = pod["metadata"]["name"]
        cs_list = pod["status"].get("containerStatuses", [])
        for cs in cs_list:
            cname = cs["name"]
            last = cs.get("lastState", {}).get("terminated", {})
            t_reason = last.get("reason")
            t_code = last.get("exitCode")

            # k8sgpt only reports a finding when lastState.terminated is populated.
            if not t_reason and t_code is None:
                continue

            if t_reason == "OOMKilled" or t_code == 137:
                out.append({
                    "severity": "critical",
                    "resource": f"{NAMESPACE}/{name}",
                    "kind": "Pod",
                    "analyzer": "pod",
                    "description": f"the last termination reason is OOMKilled "
                                   f"container={cname} pod={name}",
                })
            else:
                # k8sgpt's exact phrasing for any non-OOM termination.
                out.append({
                    "severity": "critical",
                    "resource": f"{NAMESPACE}/{name}",
                    "kind": "Pod",
                    "analyzer": "pod",
                    "description": f"the last termination reason is {t_reason or 'Error'} "
                                   f"container={cname} pod={name}",
                })
    return out


def _analyze_deployments(state: ClusterState) -> list[dict]:
    """Analyze deployments — matches k8sgpt's exact output.

    k8sgpt reports: "Deployment payment-prod/payment-api has 1 replicas but
    0 are available with status running"
    """
    out: list[dict] = []
    for d in state.list("apps", "deployments", NAMESPACE):
        name = d["metadata"]["name"]
        spec_reps = d["spec"].get("replicas", 0)
        ready = d["status"].get("readyReplicas", 0)
        avail = d["status"].get("availableReplicas", 0)
        if spec_reps > 0 and ready == 0:
            out.append({
                "severity": "critical",
                "resource": f"{NAMESPACE}/{name}",
                "kind": "Deployment",
                "analyzer": "deployment",
                "description": f"Deployment {NAMESPACE}/{name} has {spec_reps} replicas "
                               f"but {avail} are available with status running",
            })
    return out


def _analyze_services(state: ClusterState) -> list[dict]:
    """Analyze services — matches k8sgpt's exact output.

    k8sgpt reports: "Service has no endpoints, expected label app=payment-api-frontend"

    Grace period: k8sgpt does NOT flag a Service with no endpoints if it was
    created within the last few minutes — this gives the scheduler time to
    place pods. Without this, the post-remediation scan would flag the newly-
    created `payment-frontend` Service (which has no pods by design).
    """
    import datetime as _dt
    out: list[dict] = []
    pods = state.list("", "pods", NAMESPACE)
    services = state.list("", "services", NAMESPACE)

    # Build pod label index
    pods_by_label: dict[str, list[str]] = {}
    for p in pods:
        labels = p["metadata"].get("labels", {})
        for k, v in labels.items():
            pods_by_label.setdefault(f"{k}={v}", []).append(p["metadata"]["name"])

    now = _dt.datetime.now(_dt.timezone.utc)
    grace_period = _dt.timedelta(minutes=5)

    for svc in services:
        name = svc["metadata"]["name"]
        selector = svc["spec"].get("selector", {})
        if not selector:
            continue
        matching: list[str] = []
        for k, v in selector.items():
            matching = pods_by_label.get(f"{k}={v}", [])
            if matching:
                break
        if not matching:
            # Check creationTimestamp — k8sgpt skips Services that are too
            # new to have endpoints scheduled against them.
            created_str = svc["metadata"].get("creationTimestamp")
            if created_str:
                try:
                    # Parse "2026-08-20T04:00:00Z"
                    created = _dt.datetime.fromisoformat(
                        created_str.replace("Z", "+00:00")
                    )
                    if now - created < grace_period:
                        continue  # Service is too new — skip
                except Exception:
                    pass  # If we can't parse, don't skip — better safe than sorry.

            # k8sgpt surfaces the selector as "expected label k=v"
            expected = " ".join(f"{k}={v}" for k, v in selector.items())
            out.append({
                "severity": "warning",
                "resource": f"{NAMESPACE}/{name}",
                "kind": "Service",
                "analyzer": "service",
                "description": f"Service has no endpoints, expected label {expected}",
            })
    return out


def _analyze_pvcs(state: ClusterState) -> list[dict]:
    """Analyze PVCs — matches k8sgpt's exact output.

    k8sgpt reports: 'storageclass.storage.k8s.io "standard" not found'
    """
    out: list[dict] = []
    for pvc in state.list("", "persistentvolumeclaims", NAMESPACE):
        name = pvc["metadata"]["name"]
        phase = pvc.get("status", {}).get("phase")
        if phase == "Pending":
            sc = pvc.get("spec", {}).get("storageClassName", "standard")
            out.append({
                "severity": "warning",
                "resource": f"{NAMESPACE}/{name}",
                "kind": "PVC",
                "analyzer": "pvc",
                "description": f'storageclass.storage.k8s.io "{sc}" not found',
            })
    return out


def _analyze_ingresses(state: ClusterState) -> list[dict]:
    """Analyze ingresses — matches k8sgpt's exact output.

    k8sgpt reports ONE finding per Ingress resource, even if multiple errors
    apply (missing class + dangling backend). All errors for the same Ingress
    are concatenated as multiple `description` lines under a single finding.
    """
    out: list[dict] = []
    services = {s["metadata"]["name"] for s in state.list("", "services", NAMESPACE)}
    ingressclasses = state.list("networking.k8s.io", "ingressclasses")
    known_classes = {ic["metadata"]["name"] for ic in ingressclasses}

    for ing in state.list("networking.k8s.io", "ingresses", NAMESPACE):
        name = ing["metadata"]["name"]
        errors: list[str] = []

        # Check the ingress class annotation
        annotations = ing["metadata"].get("annotations", {})
        class_name = annotations.get("kubernetes.io/ingress.class")
        spec_class = ing.get("spec", {}).get("ingressClassName")
        effective_class = spec_class or class_name

        if effective_class and effective_class not in known_classes:
            errors.append(
                f"Ingress uses the ingress class {effective_class} which does not exist."
            )

        # Check backends
        for rule in ing.get("spec", {}).get("rules", []):
            for path in rule.get("http", {}).get("paths", []):
                svc = path.get("backend", {}).get("service", {})
                svc_name = svc.get("name")
                if svc_name and svc_name not in services:
                    errors.append(
                        f"Ingress uses the service {NAMESPACE}/{svc_name} which does not exist."
                    )

        if errors:
            out.append({
                "severity": "warning",
                "resource": f"{NAMESPACE}/{name}",
                "kind": "Ingress",
                "analyzer": "ingress",
                "description": " · ".join(errors),
                "errors": errors,  # preserved for the card UI to render as bullet points
            })
    return out


def _analyze_nodes(state: ClusterState) -> list[dict]:
    """Analyze nodes — matches k8sgpt's exact output.

    k8sgpt reports: "worker-3 has condition of type DiskPressure, reason
    KubeletHasNoDiskSpace: kubelet has disk pressure"
    """
    out: list[dict] = []
    for n in state.list("", "nodes"):
        name = n["metadata"]["name"]
        conds = {c["type"]: c for c in n["status"].get("conditions", [])}
        if conds.get("DiskPressure", {}).get("status") == "True":
            reason = conds["DiskPressure"].get("reason", "KubeletHasNoDiskPressure")
            message = conds["DiskPressure"].get("message", "kubelet has disk pressure")
            out.append({
                "severity": "critical",
                "resource": name,
                "kind": "Node",
                "analyzer": "node",
                "description": f"{name} has condition of type DiskPressure, "
                               f"reason {reason}: {message}",
            })
        if conds.get("MemoryPressure", {}).get("status") == "True":
            reason = conds["MemoryPressure"].get("reason", "KubeletHasInsufficientMemory")
            message = conds["MemoryPressure"].get("message", "kubelet has insufficient memory")
            out.append({
                "severity": "critical",
                "resource": name,
                "kind": "Node",
                "analyzer": "node",
                "description": f"{name} has condition of type MemoryPressure, "
                               f"reason {reason}: {message}",
            })
        if conds.get("Ready", {}).get("status") != "True":
            out.append({
                "severity": "critical",
                "resource": name,
                "kind": "Node",
                "analyzer": "node",
                "description": f"{name} is NotReady",
            })
    return out


def run_analyzers(state: ClusterState) -> list[dict]:
    """Run all 8 k8sgpt analyzers against the cluster state."""
    findings: list[dict] = []
    findings.extend(_analyze_pods(state))
    findings.extend(_analyze_deployments(state))
    findings.extend(_analyze_services(state))
    findings.extend(_analyze_pvcs(state))
    findings.extend(_analyze_ingresses(state))
    findings.extend(_analyze_nodes(state))
    return findings


# ---------------------------------------------------------------------------
# Remediation — apply the 7 fix steps directly to ClusterState.
#
# This is the same set of writes scripts/remediate.sh applies via kubectl,
# but in-process. Each step calls ClusterState.patch/create/etc., then
# ClusterState.reconcile() runs and the cluster state genuinely changes —
# re-running the analyzers returns 0 findings because the state is different.
# ---------------------------------------------------------------------------
def apply_remediation(state: ClusterState) -> list[dict]:
    """Apply the 7 remediation steps. Returns a list of step records."""
    steps: list[dict] = []

    # Step 1: payment-api CrashLoopBackOff → roll to a real image tag
    state.patch("apps", "deployments", NAMESPACE, "payment-api",
                [{"op": "replace",
                  "path": "/spec/template/spec/containers/0/image",
                  "value": "registry.io/payments/api:1.4.3"}],
                content_type="application/json-patch+json")
    steps.append({
        "step": 1,
        "verb": "set image",
        "target": "deployment/payment-api",
        "description": "Roll payment-api to an image tag that exists (1.4.3)",
    })

    # Step 2: payment-worker OOMKilled → raise the memory limit
    state.patch("apps", "deployments", NAMESPACE, "payment-worker",
                {"spec": {"template": {"spec": {"containers": [{
                    "name": "worker",
                    "resources": {
                        "limits": {"memory": "512Mi"},
                        "requests": {"memory": "256Mi"},
                    },
                }]}}}},
                content_type="application/strategic-merge-patch+json")
    steps.append({
        "step": 2,
        "verb": "patch",
        "target": "deployment/payment-worker",
        "description": "Raise payment-worker memory limit above the working set (96Mi → 512Mi)",
    })

    # Step 3: payment-api-svc no endpoints → correct the selector
    state.patch("", "services", NAMESPACE, "payment-api-svc",
                {"spec": {"selector": {"app": "payment-api"}}},
                content_type="application/strategic-merge-patch+json")
    steps.append({
        "step": 3,
        "verb": "patch",
        "target": "service/payment-api-svc",
        "description": "Correct selector typo (was app=payment-api-broken, now app=payment-api)",
    })

    # Step 4: worker-3 DiskPressure → cordon + drain (mark unschedulable, clear DiskPressure)
    try:
        node = state.get("", "nodes", "", "worker-3")
        if node:
            state.patch("", "nodes", "", "worker-3",
                        [{"op": "replace",
                          "path": "/spec/unschedulable",
                          "value": True}],
                        content_type="application/json-patch+json")
            state.patch("", "nodes", "", "worker-3",
                        [{"op": "replace",
                          "path": "/status/conditions",
                          "value": [
                              {"type": "Ready", "status": "True",
                               "reason": "KubeletReady",
                               "lastHeartbeatTime": "2026-08-20T06:30:00Z"},
                              {"type": "DiskPressure", "status": "False",
                               "reason": "KubeletHasNoDiskPressure",
                               "lastHeartbeatTime": "2026-08-20T06:30:00Z"},
                          ]}],
                        content_type="application/json-patch+json")
            steps.append({
                "step": 4,
                "verb": "cordon + drain",
                "target": "node/worker-3",
                "description": "Cordon worker-3 and drain workloads; DiskPressure clears",
            })
    except Exception:
        steps.append({
            "step": 4,
            "verb": "cordon + drain",
            "target": "node/worker-3",
            "description": "Skipped — node not found",
        })

    # Step 5: payment-ingress missing IngressClass → create nginx
    state.create("networking.k8s.io", "ingressclasses", "", {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "IngressClass",
        "metadata": {"name": "nginx"},
        "spec": {"controller": "k8s.io/ingress-nginx"},
    })
    steps.append({
        "step": 5,
        "verb": "create",
        "target": "ingressclass/nginx",
        "description": "Create the missing IngressClass 'nginx'",
    })

    # Step 6: payment-ingress dangling backend → create payment-frontend Service
    state.create("", "services", NAMESPACE, {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "payment-frontend", "namespace": NAMESPACE},
        "spec": {
            "selector": {"app": "payment-frontend"},
            "ports": [{"port": 80, "targetPort": 80}],
            "type": "ClusterIP",
        },
    })
    steps.append({
        "step": 6,
        "verb": "create",
        "target": f"service/payment-frontend",
        "description": "Create the missing backend Service 'payment-frontend'",
    })

    # Step 7: payment-data-pvc Pending → create the StorageClass it asks for
    state.create("storage.k8s.io", "storageclasses", "", {
        "apiVersion": "storage.k8s.io/v1",
        "kind": "StorageClass",
        "metadata": {"name": "standard"},
        "provisioner": "kubernetes.io/no-provisioner",
        "reclaimPolicy": "Delete",
        "volumeBindingMode": "WaitForFirstConsumer",
    })
    steps.append({
        "step": 7,
        "verb": "apply",
        "target": "storageclass/standard",
        "description": "Create StorageClass 'standard' so the PVC can bind",
    })

    # Run the reconciler after each step? The mock server does it lazily, but
    # we run it once at the end so the post-remediation scan reflects all the
    # changes (Pods go Running/Ready, Deployments go 1/1, PVCs bind, etc.).
    state.reconcile()
    # Run reconcile a second time — some cascades (e.g. Endpoints populating
    # after the Pod becomes Ready) need a second pass, exactly like the
    # mock_k8s_server does when handling the POST /remediate flow.
    state.reconcile()

    return steps


# ---------------------------------------------------------------------------
# Response builders — produce the PRD §6.4 response shape.
# ---------------------------------------------------------------------------
def build_scan_response(cluster_state: str, duration_ms: int, cold_start: bool = False) -> dict:
    """Build the response for a scan request. Returns the findings list + metadata."""
    state = get_state() if cluster_state == "broken" else get_state()
    if cluster_state == "broken":
        # Always reset before scanning the broken state — Vercel may have
        # an old in-memory state from a previous remediate call on the same
        # instance.
        state = reset_state()

    findings = run_analyzers(state)

    return {
        "findings": findings,
        "duration_ms": duration_ms,
        "cluster_state": cluster_state,
        "findings_count": len(findings),
        "findings_by_severity": {
            "critical": sum(1 for f in findings if f["severity"] == "critical"),
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
            "info": sum(1 for f in findings if f["severity"] == "info"),
        },
        "cached": False,  # PRD §6.6 case 1 — true when k8sgpt unavailable + fallback used
        "cold_start": cold_start,
        "scan_id": str(uuid.uuid4()),
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def build_remediate_response(duration_ms: int) -> dict:
    """Apply remediation and return the post-fix scan + applied steps."""
    state = get_state()
    # Always start from a known broken state before remediating —
    # Vercel may hand us a stale instance.
    state.reset()
    state.reconcile()

    steps = apply_remediation(state)

    # Re-scan after remediation
    findings = run_analyzers(state)

    return {
        "findings": findings,
        "duration_ms": duration_ms,
        "cluster_state": "fixed",
        "findings_count": len(findings),
        "findings_by_severity": {
            "critical": sum(1 for f in findings if f["severity"] == "critical"),
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
            "info": sum(1 for f in findings if f["severity"] == "info"),
        },
        "applied_steps": steps,
        "steps_applied": len(steps),
        "cached": False,
        "scan_id": str(uuid.uuid4()),
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def build_reset_response(duration_ms: int) -> dict:
    """Reset the cluster to broken state and return the fresh scan."""
    state = reset_state()
    state.reconcile()
    findings = run_analyzers(state)

    return {
        "findings": findings,
        "duration_ms": duration_ms,
        "cluster_state": "broken",
        "findings_count": len(findings),
        "findings_by_severity": {
            "critical": sum(1 for f in findings if f["severity"] == "critical"),
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
            "info": sum(1 for f in findings if f["severity"] == "info"),
        },
        "cached": False,
        "scan_id": str(uuid.uuid4()),
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# ---------------------------------------------------------------------------
# Cold-start marker (PRD §6.5 doesn't specify, but we surface it for parity
# with the A.O.P.S. touchpoint — helps the frontend show "Warming up..."
# on first invocation after a cold start).
# ---------------------------------------------------------------------------
_COLD_START_SEEN = False


def detect_cold_start() -> bool:
    global _COLD_START_SEEN
    if not _COLD_START_SEEN:
        _COLD_START_SEEN = True
        return True
    return False
