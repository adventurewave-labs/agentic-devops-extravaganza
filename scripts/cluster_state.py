"""
Mutable cluster state for the mock Kubernetes API server.

This is what makes the remediation demo real rather than a slideshow: the
mock accepts genuine `kubectl` writes (POST / PUT / PATCH / DELETE), applies
Kubernetes patch semantics to the stored objects, and then runs a small
reconciler that models what the real control plane would do in response.

Nothing here is pre-scripted. `kubectl set image deploy/payment-api ...`
changes the stored Deployment; the reconciler sees the image is no longer the
broken tag and rolls the Deployment and its Pod to a healthy state; the next
`k8sgpt analyze` then finds two fewer problems because the *state it reads is
actually different*. Run the commands in a different order, or fix only some
of them, and you get a correspondingly different finding count.

Simulated controller behaviours are enumerated in RECONCILER_RULES below and
in README.md. They are deliberately explicit: this is a simulator, and the
places where it simulates are documented rather than hidden.
"""
import copy
import re
import threading
from datetime import datetime, timezone

import cluster_fixtures as fx

# Every rule the reconciler applies, in plain language. README renders this
# table; run_uat.py asserts each one.
RECONCILER_RULES = [
    ("payment-api CrashLoopBackOff",
     "spec.template.spec.containers[api].image != " + fx.BROKEN_API_IMAGE,
     "Deployment goes 1/1 available; Pod goes Running/Ready; Endpoints populate"),
    ("payment-worker OOMKilled",
     f"container[worker] memory limit >= {fx.MIN_HEALTHY_WORKER_MEMORY_MI}Mi",
     "Deployment goes 1/1 available; Pod goes Running/Ready"),
    ("worker-3 DiskPressure",
     "node cordoned (spec.unschedulable=true) or DiskPressure patched to False",
     "DiskPressure clears to False/KubeletHasNoDiskPressure (models drain + disk reclaim)"),
    ("payment-ingress dangling backend",
     f"Service {fx.MISSING_BACKEND_SERVICE} created in payment-prod",
     "Ingress backend resolves; k8sgpt stops flagging it"),
    ("payment-ingress missing class",
     f"IngressClass {fx.MISSING_INGRESS_CLASS} created",
     "Ingress class resolves; k8sgpt stops flagging it"),
    ("payment-data-pvc Pending",
     f"StorageClass {fx.MISSING_STORAGE_CLASS} created",
     "PVC binds (phase=Bound) and a backing PV is materialised"),
    ("payment-api-svc has no endpoints",
     "Service selector corrected to match the Pod labels (app=payment-api)",
     "Endpoints populate once the Pod is also Ready"),
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_memory_mi(value) -> float:
    """Parse a Kubernetes memory quantity into MiB. Returns -1 if unparseable."""
    if value is None:
        return -1.0
    s = str(value).strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(Ki|Mi|Gi|Ti|K|M|G|T|)?", s)
    if not m:
        return -1.0
    num = float(m.group(1))
    unit = m.group(2) or ""
    factor = {
        "": 1.0 / (1024 * 1024), "Ki": 1.0 / 1024, "Mi": 1.0, "Gi": 1024.0,
        "Ti": 1024.0 * 1024, "K": 1000.0 / (1024 * 1024),
        "M": 1000.0 ** 2 / (1024 * 1024), "G": 1000.0 ** 3 / (1024 * 1024),
        "T": 1000.0 ** 4 / (1024 * 1024),
    }[unit]
    return num * factor


def _strategic_merge(target, patch):
    """Apply a strategic-merge / merge patch in place.

    Handles the subset kubectl actually emits for this demo:
      - nested object merge
      - null value deletes the key (RFC 7386)
      - lists of objects carrying a `name` key merge by name (containers,
        conditions, ports) rather than being replaced wholesale
      - `$setElementOrder/...` and `$patch` directives are ignored
    """
    if not isinstance(patch, dict):
        return patch
    if not isinstance(target, dict):
        target = {}
    for key, value in patch.items():
        if key.startswith("$setElementOrder/") or key == "$patch":
            continue
        if key.startswith("$deleteFromPrimitiveList/"):
            continue
        if value is None:
            target.pop(key, None)
            continue
        if isinstance(value, dict):
            target[key] = _strategic_merge(target.get(key), value)
        elif isinstance(value, list):
            target[key] = _merge_list(target.get(key), value)
        else:
            target[key] = value
    return target


def _merge_list(current, patch_list):
    """Merge a list. Objects with a `name` key merge by name; else replace."""
    if not isinstance(current, list):
        return copy.deepcopy(patch_list)
    keyed = all(isinstance(i, dict) and "name" in i for i in patch_list) and patch_list
    if not keyed:
        return copy.deepcopy(patch_list)
    if not all(isinstance(i, dict) and "name" in i for i in current):
        return copy.deepcopy(patch_list)
    by_name = {i["name"]: i for i in current}
    for item in patch_list:
        name = item["name"]
        if name in by_name:
            _strategic_merge(by_name[name], item)
        else:
            current.append(copy.deepcopy(item))
    return current


def _json_patch(target, ops):
    """Apply an RFC 6902 JSON patch (add / replace / remove) in place."""
    for op in ops:
        path = op.get("path", "")
        parts = [p.replace("~1", "/").replace("~0", "~")
                 for p in path.split("/") if p != ""]
        if not parts:
            continue
        node = target
        for p in parts[:-1]:
            node = node[int(p)] if isinstance(node, list) else node.setdefault(p, {})
        last = parts[-1]
        action = op.get("op")
        if isinstance(node, list):
            idx = len(node) if last == "-" else int(last)
            if action == "remove":
                node.pop(idx)
            elif action == "add":
                node.insert(idx, op.get("value"))
            elif action == "replace":
                node[idx] = op.get("value")
        else:
            if action == "remove":
                node.pop(last, None)
            else:
                node[last] = op.get("value")
    return target


class ClusterState:
    """Thread-safe, mutable, reconciling store of Kubernetes objects."""

    def __init__(self):
        self._lock = threading.RLock()
        self.resource_version = 184523
        self.remediation_log = []
        self.collections = fx.initial_collections()
        self.reconcile()

    # ---- lifecycle ---------------------------------------------------------

    def reset(self):
        """Restore the pristine broken cluster. Used by /reset, demos and CI."""
        with self._lock:
            self.collections = fx.initial_collections()
            self.remediation_log = []
            self.resource_version = 184523
            self.reconcile()

    def _bump(self, obj=None):
        self.resource_version += 1
        if obj is not None:
            obj.setdefault("metadata", {})["resourceVersion"] = str(self.resource_version)

    # ---- CRUD --------------------------------------------------------------

    def _collection(self, group, plural):
        return self.collections.setdefault((group, plural), [])

    def list(self, group, plural, namespace=None):
        with self._lock:
            items = self._collection(group, plural)
            if namespace:
                items = [i for i in items
                         if i.get("metadata", {}).get("namespace") == namespace]
            return copy.deepcopy(items)

    def get(self, group, plural, namespace, name):
        with self._lock:
            for item in self._collection(group, plural):
                meta = item.get("metadata", {})
                if meta.get("name") != name:
                    continue
                if namespace and meta.get("namespace") != namespace:
                    continue
                return copy.deepcopy(item)
            return None

    def create(self, group, plural, namespace, obj):
        with self._lock:
            meta = obj.setdefault("metadata", {})
            name = meta.get("name") or meta.get("generateName", "obj") + "xxxxx"
            meta["name"] = name
            if namespace:
                meta["namespace"] = namespace
            meta.setdefault("uid", f"{fx.UID_BASE}-{abs(hash((plural, name))) % 10**6}")
            meta.setdefault("creationTimestamp", _now())
            existing = self.get(group, plural, namespace, name)
            if existing is not None:
                return None, 409
            self._default_status(plural, obj)
            self._bump(obj)
            self._collection(group, plural).append(obj)
            self.reconcile()
            return self.get(group, plural, namespace, name), 201

    def replace(self, group, plural, namespace, name, obj):
        with self._lock:
            coll = self._collection(group, plural)
            for idx, item in enumerate(coll):
                meta = item.get("metadata", {})
                if meta.get("name") == name and (
                        not namespace or meta.get("namespace") == namespace):
                    obj.setdefault("metadata", {}).setdefault("uid", meta.get("uid"))
                    obj["metadata"].setdefault("creationTimestamp",
                                               meta.get("creationTimestamp"))
                    self._bump(obj)
                    coll[idx] = obj
                    self.reconcile()
                    return self.get(group, plural, namespace, name), 200
            return None, 404

    def patch(self, group, plural, namespace, name, patch_body, content_type):
        with self._lock:
            for item in self._collection(group, plural):
                meta = item.get("metadata", {})
                if meta.get("name") != name:
                    continue
                if namespace and meta.get("namespace") != namespace:
                    continue
                if "json-patch" in content_type:
                    _json_patch(item, patch_body)
                else:
                    # strategic-merge and merge-patch both handled here
                    _strategic_merge(item, patch_body)
                self._bump(item)
                self.reconcile()
                return self.get(group, plural, namespace, name), 200
            return None, 404

    def delete(self, group, plural, namespace, name):
        with self._lock:
            coll = self._collection(group, plural)
            for idx, item in enumerate(coll):
                meta = item.get("metadata", {})
                if meta.get("name") == name and (
                        not namespace or meta.get("namespace") == namespace):
                    removed = coll.pop(idx)
                    self.resource_version += 1
                    self.reconcile()
                    return removed, 200
            return None, 404

    def _default_status(self, plural, obj):
        """Give freshly-created objects a plausible status."""
        if plural == "services":
            obj.setdefault("spec", {}).setdefault("type", "ClusterIP")
            obj["spec"].setdefault("clusterIP", "10.96.42.17")
            obj.setdefault("status", {"loadBalancer": {}})
        elif plural == "storageclasses":
            obj.setdefault("provisioner", "rancher.io/local-path")
            obj.setdefault("reclaimPolicy", "Delete")
            obj.setdefault("volumeBindingMode", "Immediate")
        elif plural == "ingressclasses":
            obj.setdefault("spec", {}).setdefault("controller", "k8s.io/ingress-nginx")
        elif plural == "persistentvolumes":
            obj.setdefault("status", {"phase": "Available"})

    # ---- reconciler --------------------------------------------------------

    def reconcile(self):
        """Model the control-plane response to whatever the state now says.

        Idempotent and order-independent: it always derives status purely from
        current spec, so it produces the same result whether you fix one thing
        or six, and in any order.
        """
        self._reconcile_payment_api()
        self._reconcile_payment_worker()
        self._reconcile_node_disk_pressure()
        self._reconcile_pvc_binding()

    def _find(self, group, plural, name):
        for item in self._collection(group, plural):
            if item.get("metadata", {}).get("name") == name:
                return item
        return None

    def _container(self, obj, path_spec, container_name):
        spec = obj
        for key in path_spec:
            spec = (spec or {}).get(key, {})
        for c in spec.get("containers", []) if isinstance(spec, dict) else []:
            if c.get("name") == container_name:
                return c
        return None

    def _mark_deployment(self, deploy, healthy, message):
        replicas = deploy.get("spec", {}).get("replicas", 1)
        status = deploy.setdefault("status", {})
        if healthy:
            status.update({
                "replicas": replicas, "updatedReplicas": replicas,
                "readyReplicas": replicas, "availableReplicas": replicas,
                "unavailableReplicas": 0,
                "conditions": [
                    {"type": "Available", "status": "True",
                     "reason": "MinimumReplicasAvailable",
                     "message": "Deployment has minimum availability.",
                     "lastTransitionTime": _now()},
                    {"type": "Progressing", "status": "True",
                     "reason": "NewReplicaSetAvailable", "message": message,
                     "lastTransitionTime": _now()},
                ],
            })
        else:
            status.update({
                "replicas": replicas, "updatedReplicas": replicas,
                "readyReplicas": 0, "availableReplicas": 0,
                "unavailableReplicas": replicas,
            })

    def _mark_pod_healthy(self, pod, image):
        pod["status"] = {
            "phase": "Running",
            "conditions": [
                {"type": "Initialized", "status": "True", "lastTransitionTime": _now()},
                {"type": "Ready", "status": "True", "lastTransitionTime": _now()},
                {"type": "ContainersReady", "status": "True", "lastTransitionTime": _now()},
                {"type": "PodScheduled", "status": "True", "lastTransitionTime": _now()},
            ],
            "containerStatuses": [{
                "name": pod["spec"]["containers"][0]["name"],
                "state": {"running": {"startedAt": _now()}},
                "ready": True,
                "restartCount": 0,
                "image": image,
                "imageID": f"{image.split(':')[0]}@sha256:reconciled",
                "containerID": "containerd://reconciled",
            }],
            "qosClass": "Burstable",
        }

    def _log_remediation(self, key, detail):
        if key not in [r["key"] for r in self.remediation_log]:
            self.remediation_log.append(
                {"key": key, "detail": detail, "at": _now()})

    def _reconcile_payment_api(self):
        deploy = self._find("apps", "deployments", "payment-api")
        pod = self._find("", "pods", "payment-api-7c4f5b-x9qkl")
        if not deploy:
            return
        container = self._container(deploy, ("spec", "template", "spec"), "api")
        image = (container or {}).get("image", fx.BROKEN_API_IMAGE)
        fixed = image != fx.BROKEN_API_IMAGE
        self._mark_deployment(deploy, fixed, f'ReplicaSet rolled to image "{image}"')
        if not pod:
            return
        if fixed:
            pod["spec"]["containers"][0]["image"] = image
            self._mark_pod_healthy(pod, image)
            self._sync_endpoints(ready=True)
            self._log_remediation("payment-api", f"image set to {image}")
        else:
            self._sync_endpoints(ready=False)

    def _reconcile_payment_worker(self):
        deploy = self._find("apps", "deployments", "payment-worker")
        pod = self._find("", "pods", "payment-worker-6d8b2c-p3mnr")
        if not deploy:
            return
        container = self._container(deploy, ("spec", "template", "spec"), "worker")
        limit = (container or {}).get("resources", {}).get("limits", {}).get("memory")
        mi = _parse_memory_mi(limit)
        fixed = mi >= fx.MIN_HEALTHY_WORKER_MEMORY_MI
        self._mark_deployment(deploy, fixed, f"memory limit now {limit}")
        if not pod:
            return
        if fixed:
            pod["spec"]["containers"][0].setdefault("resources", {}).setdefault(
                "limits", {})["memory"] = limit
            self._mark_pod_healthy(pod, pod["spec"]["containers"][0]["image"])
            self._log_remediation("payment-worker", f"memory limit raised to {limit}")

    def _reconcile_node_disk_pressure(self):
        node = self._find("", "nodes", "worker-3")
        if not node:
            return
        cordoned = node.get("spec", {}).get("unschedulable") is True
        conditions = node.setdefault("status", {}).setdefault("conditions", [])
        disk = next((c for c in conditions if c.get("type") == "DiskPressure"), None)
        if disk is None:
            return
        if cordoned and disk.get("status") == "True":
            # Models: node drained -> kubelet reclaims image/container disk ->
            # DiskPressure clears. Documented in RECONCILER_RULES.
            disk.update({
                "status": "False",
                "reason": "KubeletHasNoDiskPressure",
                "message": "kubelet has no disk pressure",
                "lastTransitionTime": _now(),
            })
            self._log_remediation("worker-3", "cordoned/drained; DiskPressure cleared")
        elif disk.get("status") == "False":
            self._log_remediation("worker-3", "DiskPressure patched to False")

    def _reconcile_pvc_binding(self):
        pvc = self._find("", "persistentvolumeclaims", "payment-data-pvc")
        if not pvc:
            return
        wanted = pvc.get("spec", {}).get("storageClassName")
        classes = [c.get("metadata", {}).get("name")
                   for c in self._collection("storage.k8s.io", "storageclasses")]
        if wanted in classes:
            if pvc["status"].get("phase") != "Bound":
                pvc["status"] = {
                    "phase": "Bound",
                    "accessModes": pvc["spec"].get("accessModes", []),
                    "capacity": pvc["spec"].get("resources", {}).get("requests", {}),
                    "conditions": [],
                }
                pvc["spec"]["volumeName"] = "pvc-payment-data"
                self._log_remediation(
                    "payment-data-pvc", f'bound to StorageClass "{wanted}"')
        else:
            pvc["status"] = {
                "phase": "Pending",
                "conditions": [
                    {"type": "FileSystemResizePending", "status": "True"},
                    {"type": "Ready", "status": "False", "reason": "ProvisioningFailed"},
                ],
            }

    def _sync_endpoints(self, ready: bool):
        """Populate Endpoints only when a Service selector genuinely matches.

        The payment-api-svc selector is `app=payment-api-frontend` while the
        Pod carries `app=payment-api`, so a healthy Pod alone is NOT enough —
        exactly as in a real cluster. Fixing the image populates nothing until
        the selector is also corrected, which is why k8sgpt keeps reporting
        "Service has no endpoints" until both writes land.
        """
        ep = self._find("", "endpoints", "payment-api-svc")
        svc = self._find("", "services", "payment-api-svc")
        pod = self._find("", "pods", "payment-api-7c4f5b-x9qkl")
        if not ep:
            return
        selector = (svc or {}).get("spec", {}).get("selector", {})
        pod_labels = (pod or {}).get("metadata", {}).get("labels", {})
        selector_matches = bool(selector) and all(
            pod_labels.get(k) == v for k, v in selector.items())
        ready = ready and selector_matches
        if ready:
            ep["subsets"] = [{
                "addresses": [{"ip": "10.244.1.37",
                               "targetRef": {"kind": "Pod",
                                             "name": "payment-api-7c4f5b-x9qkl",
                                             "namespace": "payment-prod"}}],
                "ports": [{"port": 8080, "protocol": "TCP"}],
            }]
        else:
            ep["subsets"] = []

    # ---- introspection for demos / UAT -------------------------------------

    def health_summary(self):
        """A compact, honest snapshot the demo scripts render as before/after."""
        with self._lock:
            deploys = self._collection("apps", "deployments")
            pods = self._collection("", "pods")
            node = self._find("", "nodes", "worker-3")
            pvc = self._find("", "persistentvolumeclaims", "payment-data-pvc")
            disk = next((c for c in (node or {}).get("status", {}).get("conditions", [])
                         if c.get("type") == "DiskPressure"), {})
            ready_pods = sum(
                1 for p in pods
                if all(cs.get("ready") for cs in p.get("status", {}).get("containerStatuses", []))
            )
            available = sum(d.get("status", {}).get("availableReplicas", 0) for d in deploys)
            desired = sum(d.get("spec", {}).get("replicas", 0) for d in deploys)
            return {
                "pods_ready": f"{ready_pods}/{len(pods)}",
                "deployments_available": f"{available}/{desired}",
                "worker3_disk_pressure": disk.get("status", "Unknown"),
                "pvc_phase": pvc.get("status", {}).get("phase") if pvc else "Absent",
                "ingress_class_exists": any(
                    c.get("metadata", {}).get("name") == fx.MISSING_INGRESS_CLASS
                    for c in self._collection("networking.k8s.io", "ingressclasses")),
                "backend_service_exists": any(
                    s.get("metadata", {}).get("name") == fx.MISSING_BACKEND_SERVICE
                    for s in self._collection("", "services")),
                "remediations_applied": len(self.remediation_log),
            }
