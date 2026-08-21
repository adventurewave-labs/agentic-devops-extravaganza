"""
Fixture data for the deliberately-broken `payment-prod` cluster.

This module holds ONLY inert data — the pristine, broken starting state that
`cluster_state.ClusterState` deep-copies at boot and after every `/reset`.
Nothing here mutates; all mutation lives in cluster_state.py.

The cluster "story":

  Namespace: payment-prod
    - Pod payment-api-7c4f5b-x9qkl      CrashLoopBackOff (image tag does not exist)
    - Pod payment-worker-6d8b2c-p3mnr   OOMKilled (memory limit too low)
    - Deployment payment-api            0/1 available
    - Deployment payment-worker         0/1 available
    - Service payment-api-svc           no endpoints (selector mismatch)
    - PVC payment-data-pvc              Pending (StorageClass "standard" not registered)
    - Ingress payment-ingress           ingressClass "nginx" missing AND
                                        backend Service "payment-frontend" missing

  Cluster-scoped
    - Node worker-3                     DiskPressure=True
    - Node worker-1                     Ready

Each of these maps to a k8sgpt finding, and each has a corresponding real
`kubectl` remediation that cluster_state.py's reconciler honours.
"""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------- Realistic "broken cluster" state ----------

NOW = "2026-08-20T04:00:00Z"
UID_BASE = "f47ac10b-58cc-4372-a567-0e02b2c3d479"

PAYMENT_API_POD = {
    "apiVersion": "v1",
    "kind": "Pod",
    "metadata": {
        "name": "payment-api-7c4f5b-x9qkl",
        "namespace": "payment-prod",
        "uid": UID_BASE + "-01",
        "creationTimestamp": "2026-08-20T03:32:11Z",
        "labels": {
            "app": "payment-api",
            "pod-template-hash": "7c4f5b",
        },
        "annotations": {"kubectl.kubernetes.io/restartedAt": "2026-08-20T03:32:00Z"},
        "ownerReferences": [{
            "apiVersion": "apps/v1",
            "kind": "ReplicaSet",
            "name": "payment-api-7c4f5b",
            "uid": UID_BASE + "-rs",
            "controller": True,
            "blockOwnerDeletion": True,
        }],
    },
    "spec": {
        "containers": [{
            "name": "api",
            "image": "registry.io/payments/api:1.4.2",  # tag does not exist
            "ports": [{"containerPort": 8080, "protocol": "TCP"}],
            "env": [{"name": "DB_PASSWORD",
                     "valueFrom": {"secretKeyRef": {"name": "payment-api-secret",
                                                    "key": "password"}}}],
            "resources": {
                "requests": {"cpu": "100m", "memory": "128Mi"},
                "limits": {"cpu": "500m", "memory": "256Mi"},
            },
        }],
        "nodeName": "worker-1",
    },
    "status": {
        "phase": "Running",
        "conditions": [
            {"type": "Initialized", "status": "True", "lastProbeTime": None,
             "lastTransitionTime": "2026-08-20T03:32:11Z"},
            {"type": "Ready", "status": "False", "reason": "ContainersNotReady",
             "message": "Containers with unready status: [api]",
             "lastTransitionTime": "2026-08-20T03:34:02Z"},
            {"type": "ContainersReady", "status": "False", "reason": "ContainersNotReady",
             "lastTransitionTime": "2026-08-20T03:34:02Z"},
            {"type": "PodScheduled", "status": "True",
             "lastTransitionTime": "2026-08-20T03:32:11Z"},
        ],
        "containerStatuses": [{
            "name": "api",
            "state": {
                "waiting": {
                    "reason": "CrashLoopBackOff",
                    "message": "back-off 5m0s restarting failed container=api "
                               "pod=payment-api-7c4f5b-x9qkl_payment-prod",
                }
            },
            "lastState": {
                "terminated": {
                    "exitCode": 1,
                    "reason": "Error",
                    "message": "Error: ImagePullBackOff: "
                               "Back-off pulling image \"registry.io/payments/api:1.4.2\"",
                    "startedAt": "2026-08-20T03:33:58Z",
                    "finishedAt": "2026-08-20T03:34:00Z",
                    "containerID": "containerd://a1b2c3d4e5f6",
                }
            },
            "ready": False,
            "restartCount": 7,
            "image": "registry.io/payments/api:1.4.2",
            "imageID": "",
            "containerID": "containerd://a1b2c3d4e5f6",
        }],
        "qosClass": "Burstable",
    },
}

PAYMENT_WORKER_POD = {
    "apiVersion": "v1",
    "kind": "Pod",
    "metadata": {
        "name": "payment-worker-6d8b2c-p3mnr",
        "namespace": "payment-prod",
        "uid": UID_BASE + "-02",
        "creationTimestamp": "2026-08-20T03:30:00Z",
        "labels": {"app": "payment-worker", "pod-template-hash": "6d8b2c"},
        "ownerReferences": [{
            "apiVersion": "apps/v1",
            "kind": "ReplicaSet",
            "name": "payment-worker-6d8b2c",
            "uid": UID_BASE + "-rs2",
            "controller": True,
        }],
    },
    "spec": {
        "containers": [{
            "name": "worker",
            "image": "registry.io/payments/worker:2.1.0",
            "resources": {
                "requests": {"cpu": "50m", "memory": "64Mi"},
                "limits": {"cpu": "100m", "memory": "96Mi"},  # too tight -> OOM
            },
        }],
        "nodeName": "worker-1",
    },
    "status": {
        "phase": "Running",
        "conditions": [
            {"type": "Initialized", "status": "True"},
            {"type": "Ready", "status": "False", "reason": "ContainersNotReady"},
            {"type": "ContainersReady", "status": "False", "reason": "ContainersNotReady"},
            {"type": "PodScheduled", "status": "True"},
        ],
        "containerStatuses": [{
            "name": "worker",
            "state": {"waiting": {"reason": "CrashLoopBackOff",
                                 "message": "back-off 2m0s restarting failed container=worker"}},
            "lastState": {
                "terminated": {
                    "exitCode": 137,  # OOMKilled
                    "reason": "OOMKilled",
                    "message": "container killed by OOMKilled",
                    "startedAt": "2026-08-20T03:31:00Z",
                    "finishedAt": "2026-08-20T03:31:42Z",
                    "containerID": "containerd://b2c3d4e5f6a7",
                }
            },
            "ready": False,
            "restartCount": 12,
            "image": "registry.io/payments/worker:2.1.0",
            "imageID": "registry.io/payments/worker@sha256:abc123",
            "containerID": "containerd://b2c3d4e5f6a7",
        }],
        "qosClass": "Burstable",
    },
}

# Deployments
PAYMENT_API_DEPLOY = {
    "apiVersion": "apps/v1",
    "kind": "Deployment",
    "metadata": {
        "name": "payment-api",
        "namespace": "payment-prod",
        "uid": UID_BASE + "-dep1",
        "creationTimestamp": "2026-08-20T03:30:00Z",
        "labels": {"app": "payment-api"},
        "annotations": {"deployment.kubernetes.io/revision": "3"},
    },
    "spec": {
        "replicas": 1,
        "selector": {"matchLabels": {"app": "payment-api"}},
        "template": {
            "metadata": {"labels": {"app": "payment-api"}},
            "spec": PAYMENT_API_POD["spec"],
        },
    },
    "status": {
        "observedGeneration": 3,
        "replicas": 1,
        "updatedReplicas": 1,
        "readyReplicas": 0,
        "availableReplicas": 0,
        "unavailableReplicas": 1,
        "conditions": [
            {"type": "Available", "status": "False", "reason": "MinimumReplicasUnavailable",
             "message": "Deployment does not have minimum availability.",
             "lastTransitionTime": "2026-08-20T03:34:00Z"},
            {"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded",
             "message": "ReplicaSet \"payment-api-7c4f5b\" has timed out progressing.",
             "lastTransitionTime": "2026-08-20T03:44:00Z"},
        ],
    },
}

PAYMENT_WORKER_DEPLOY = {
    "apiVersion": "apps/v1",
    "kind": "Deployment",
    "metadata": {
        "name": "payment-worker",
        "namespace": "payment-prod",
        "uid": UID_BASE + "-dep2",
        "creationTimestamp": "2026-08-20T03:30:00Z",
        "labels": {"app": "payment-worker"},
    },
    "spec": {
        "replicas": 1,
        "selector": {"matchLabels": {"app": "payment-worker"}},
        "template": {
            "metadata": {"labels": {"app": "payment-worker"}},
            "spec": PAYMENT_WORKER_POD["spec"],
        },
    },
    "status": {
        "observedGeneration": 2,
        "replicas": 1,
        "updatedReplicas": 1,
        "readyReplicas": 0,
        "availableReplicas": 0,
        "unavailableReplicas": 1,
        "conditions": [
            {"type": "Available", "status": "False", "reason": "MinimumReplicasUnavailable"},
            {"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded"},
        ],
    },
}

# ReplicaSets
PAYMENT_API_RS = {
    "apiVersion": "apps/v1",
    "kind": "ReplicaSet",
    "metadata": {
        "name": "payment-api-7c4f5b",
        "namespace": "payment-prod",
        "uid": UID_BASE + "-rs",
        "creationTimestamp": "2026-08-20T03:32:00Z",
        "labels": {"app": "payment-api", "pod-template-hash": "7c4f5b"},
        "ownerReferences": [{
            "apiVersion": "apps/v1", "kind": "Deployment",
            "name": "payment-api", "uid": UID_BASE + "-dep1",
            "controller": True,
        }],
    },
    "spec": {
        "replicas": 1,
        "selector": {"matchLabels": {"app": "payment-api", "pod-template-hash": "7c4f5b"}},
    },
    "status": {
        "observedGeneration": 1,
        "replicas": 1,
        "fullyLabeledReplicas": 1,
        "readyReplicas": 0,
        "availableReplicas": 0,
    },
}

PAYMENT_WORKER_RS = {
    "apiVersion": "apps/v1",
    "kind": "ReplicaSet",
    "metadata": {
        "name": "payment-worker-6d8b2c",
        "namespace": "payment-prod",
        "uid": UID_BASE + "-rs2",
        "creationTimestamp": "2026-08-20T03:30:00Z",
        "labels": {"app": "payment-worker", "pod-template-hash": "6d8b2c"},
        "ownerReferences": [{
            "apiVersion": "apps/v1", "kind": "Deployment",
            "name": "payment-worker", "uid": UID_BASE + "-dep2",
            "controller": True,
        }],
    },
    "spec": {
        "replicas": 1,
        "selector": {"matchLabels": {"app": "payment-worker", "pod-template-hash": "6d8b2c"}},
    },
    "status": {"replicas": 1, "readyReplicas": 0, "availableReplicas": 0},
}

# Services - payment-api-svc has wrong selector (no matching pods => no endpoints)
PAYMENT_API_SVC = {
    "apiVersion": "v1",
    "kind": "Service",
    "metadata": {
        "name": "payment-api-svc",
        "namespace": "payment-prod",
        "uid": UID_BASE + "-svc1",
        "creationTimestamp": "2026-08-20T03:30:00Z",
        "labels": {"app": "payment-api"},
    },
    "spec": {
        "ports": [{"name": "http", "port": 80, "targetPort": 8080, "protocol": "TCP"}],
        "selector": {"app": "payment-api-frontend"},  # MISMATCH - no pod has this label
        "clusterIP": "10.96.34.12",
        "type": "ClusterIP",
        "sessionAffinity": "None",
    },
    "status": {"loadBalancer": {}},
}

# Endpoints for payment-api-svc - EMPTY because selector matches nothing
PAYMENT_API_ENDPOINTS = {
    "apiVersion": "v1",
    "kind": "Endpoints",
    "metadata": {"name": "payment-api-svc", "namespace": "payment-prod",
                 "uid": UID_BASE + "-ep1"},
    "subsets": [],  # empty - no backing pods
}

# PVC pending (no storage class found)
PAYMENT_PVC = {
    "apiVersion": "v1",
    "kind": "PersistentVolumeClaim",
    "metadata": {
        "name": "payment-data-pvc",
        "namespace": "payment-prod",
        "uid": UID_BASE + "-pvc1",
        "creationTimestamp": "2026-08-20T03:25:00Z",
        "labels": {"app": "payment-api"},
        "annotations": {"volume.beta.kubernetes.io/storage-provisioner": "standard"},
    },
    "spec": {
        "accessModes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": "10Gi"}},
        "storageClassName": "standard",  # not registered on cluster
        "volumeMode": "Filesystem",
    },
    "status": {
        "phase": "Pending",
        "conditions": [
            {"type": "FileSystemResizePending", "status": "True"},
            {"type": "Ready", "status": "False", "reason": "ProvisioningFailed"},
        ],
    },
}

# Ingress pointing at a non-existent service
PAYMENT_INGRESS = {
    "apiVersion": "networking.k8s.io/v1",
    "kind": "Ingress",
    "metadata": {
        "name": "payment-ingress",
        "namespace": "payment-prod",
        "uid": UID_BASE + "-ing1",
        "creationTimestamp": "2026-08-20T03:25:00Z",
        "labels": {"app": "payment"},
    },
    "spec": {
        "ingressClassName": "nginx",
        "rules": [{
            "host": "pay.internal.acme.io",
            "http": {
                "paths": [{
                    "path": "/",
                    "pathType": "Prefix",
                    "backend": {
                        "service": {
                            "name": "payment-frontend",  # DOES NOT EXIST
                            "port": {"number": 80},
                        }
                    },
                }],
            },
        }],
    },
    "status": {"loadBalancer": {"ingress": [{"ip": "10.96.34.50"}]}},
}

# Nodes - worker-3 has DiskPressure
NODE_WORKER_1 = {
    "apiVersion": "v1",
    "kind": "Node",
    "metadata": {
        "name": "worker-1",
        "uid": UID_BASE + "-n1",
        "creationTimestamp": "2026-08-15T08:00:00Z",
        "labels": {
            "kubernetes.io/hostname": "worker-1",
            "kubernetes.io/os": "linux",
            "kubernetes.io/arch": "amd64",
            "node-role.kubernetes.io/worker": "",
        },
    },
    "spec": {
        "podCIDR": "10.244.1.0/24",
        "providerID": "kind://docker/kind/kind-worker-1",
    },
    "status": {
        "conditions": [
            {"type": "Ready", "status": "True", "lastTransitionTime": "2026-08-15T08:00:30Z"},
            {"type": "MemoryPressure", "status": "False"},
            {"type": "DiskPressure", "status": "False"},
            {"type": "PIDPressure", "status": "False"},
            {"type": "NetworkUnavailable", "status": "False"},
        ],
        "capacity": {"cpu": "4", "memory": "8Gi", "pods": "110"},
        "allocatable": {"cpu": "3500m", "memory": "7Gi", "pods": "110"},
        "addresses": [{"type": "InternalIP", "address": "172.18.0.2"}],
        "nodeInfo": {
            "machineID": "x", "systemUUID": "x", "bootID": "x",
            "kernelVersion": "5.10.134",
            "osImage": "Ubuntu 22.04.4 LTS",
            "containerRuntimeVersion": "containerd://1.7.18",
            "kubeletVersion": "v1.30.0",
            "architecture": "amd64",
            "operatingSystem": "linux",
        },
    },
}

NODE_WORKER_3 = {
    "apiVersion": "v1",
    "kind": "Node",
    "metadata": {
        "name": "worker-3",
        "uid": UID_BASE + "-n3",
        "creationTimestamp": "2026-08-15T08:00:00Z",
        "labels": {
            "kubernetes.io/hostname": "worker-3",
            "kubernetes.io/os": "linux",
            "kubernetes.io/arch": "amd64",
            "node-role.kubernetes.io/worker": "",
        },
    },
    "spec": {
        "podCIDR": "10.244.3.0/24",
        "providerID": "kind://docker/kind/kind-worker-3",
    },
    "status": {
        "conditions": [
            {"type": "Ready", "status": "True", "lastTransitionTime": "2026-08-15T08:00:30Z"},
            {"type": "MemoryPressure", "status": "False"},
            {"type": "DiskPressure", "status": "True",  # DISK PRESSURE!
             "reason": "KubeletHasNoDiskSpace",
             "message": "kubelet has disk pressure",
             "lastTransitionTime": "2026-08-20T03:50:00Z"},
            {"type": "PIDPressure", "status": "False"},
            {"type": "NetworkUnavailable", "status": "False"},
        ],
        "capacity": {"cpu": "4", "memory": "8Gi", "pods": "110"},
        "allocatable": {"cpu": "3500m", "memory": "7Gi", "pods": "110"},
        "addresses": [{"type": "InternalIP", "address": "172.18.0.4"}],
        "nodeInfo": NODE_WORKER_1["status"]["nodeInfo"],
    },
}

# Events - correlated with the broken resources above
EVENTS = [
    {
        "metadata": {"name": "payment-api-7c4f5b-x9qkl.17a4", "namespace": "payment-prod",
                     "uid": UID_BASE + "-ev1"},
        "involvedObject": {"kind": "Pod", "namespace": "payment-prod",
                           "name": "payment-api-7c4f5b-x9qkl",
                           "uid": UID_BASE + "-01", "apiVersion": "v1"},
        "reason": "Failed",
        "message": "Error: ImagePullBackOff: Back-off pulling image "
                   "\"registry.io/payments/api:1.4.2\"",
        "source": {"component": "kubelet", "host": "worker-1"},
        "firstTimestamp": "2026-08-20T03:33:00Z",
        "lastTimestamp": "2026-08-20T03:58:00Z",
        "count": 7,
        "type": "Warning",
    },
    {
        "metadata": {"name": "payment-worker-6d8b2c-p3mnr.17b1", "namespace": "payment-prod"},
        "involvedObject": {"kind": "Pod", "namespace": "payment-prod",
                           "name": "payment-worker-6d8b2c-p3mnr",
                           "uid": UID_BASE + "-02", "apiVersion": "v1"},
        "reason": "BackOff",
        "message": "Back-off restarting failed container",
        "source": {"component": "kubelet", "host": "worker-1"},
        "firstTimestamp": "2026-08-20T03:32:00Z",
        "lastTimestamp": "2026-08-20T03:58:00Z",
        "count": 12,
        "type": "Warning",
    },
    {
        "metadata": {"name": "payment-worker-6d8b2c-p3mnr.17b2", "namespace": "payment-prod"},
        "involvedObject": {"kind": "Pod", "namespace": "payment-prod",
                           "name": "payment-worker-6d8b2c-p3mnr", "apiVersion": "v1"},
        "reason": "Killing",
        "message": "Container worker was OOMKilled (exit code 137)",
        "source": {"component": "kubelet", "host": "worker-1"},
        "firstTimestamp": "2026-08-20T03:31:42Z",
        "lastTimestamp": "2026-08-20T03:58:00Z",
        "count": 12,
        "type": "Warning",
    },
    {
        "metadata": {"name": "payment-data-pvc.17c0", "namespace": "payment-prod"},
        "involvedObject": {"kind": "PersistentVolumeClaim", "namespace": "payment-prod",
                           "name": "payment-data-pvc", "apiVersion": "v1"},
        "reason": "ProvisioningFailed",
        "message": "storageclass.storage.k8s.io \"standard\" not found",
        "source": {"component": "persistent-volume-controller"},
        "firstTimestamp": "2026-08-20T03:25:05Z",
        "lastTimestamp": "2026-08-20T03:58:00Z",
        "count": 30,
        "type": "Warning",
    },
    {
        "metadata": {"name": "worker-3.17d0", "namespace": "default"},
        "involvedObject": {"kind": "Node", "name": "worker-3", "apiVersion": "v1"},
        "reason": "NodeHasDiskPressure",
        "message": "Node worker-3 status is now: NodeHasDiskPressure",
        "source": {"component": "kubelet", "host": "worker-3"},
        "firstTimestamp": "2026-08-20T03:50:00Z",
        "lastTimestamp": "2026-08-20T03:58:00Z",
        "count": 9,
        "type": "Warning",
    },
    {
        "metadata": {"name": "payment-api-deploy.17e0", "namespace": "payment-prod"},
        "involvedObject": {"kind": "Deployment", "namespace": "payment-prod",
                           "name": "payment-api", "apiVersion": "apps/v1"},
        "reason": "DeploymentProgressing",
        "message": "ReplicaSet \"payment-api-7c4f5b\" has timed out progressing.",
        "source": {"component": "deployment-controller"},
        "firstTimestamp": "2026-08-20T03:44:00Z",
        "lastTimestamp": "2026-08-20T03:44:00Z",
        "count": 1,
        "type": "Warning",
    },
]

# Namespaces
NAMESPACES = ["default", "kube-system", "payment-prod", "ingress-nginx"]

NAMESPACES_LIST = {
    "apiVersion": "v1",
    "kind": "NamespaceList",
    "items": [{
        "apiVersion": "v1", "kind": "Namespace",
        "metadata": {"name": n, "uid": UID_BASE + f"-ns-{i}",
                     "creationTimestamp": "2026-08-15T08:00:00Z"},
        "spec": {"finalizers": ["kubernetes"]},
        "status": {"phase": "Active"},
    } for i, n in enumerate(NAMESPACES)],
}

# StorageClasses (none - to make PVC Pending realistic)
STORAGE_CLASS_LIST = {
    "apiVersion": "storage.k8s.io/v1",
    "kind": "StorageClassList",
    "items": [],
}

# API versions
API_VERSIONS = {
    "kind": "APIVersions", "versions": [
        "v1", "apps/v1", "networking.k8s.io/v1", "batch/v1",
        "storage.k8s.io/v1", "apiextensions.k8s.io/v1",
    ], "serverAddressByClientCIDRs": [],
}

# API resource discovery - what each group/version serves
API_V1_RESOURCES = ["pods", "services", "endpoints", "namespaces", "events",
                    "nodes", "persistentvolumeclaims", "configmaps", "secrets"]
APPS_V1_RESOURCES = ["deployments", "replicasets", "statefulsets", "daemonsets"]
NETWORKING_V1_RESOURCES = ["ingresses"]
BATCH_V1_RESOURCES = ["jobs", "cronjobs"]

# Map plural resource name -> Kind (handles irregular plurals like Endpoints, Ingress)
RESOURCE_KIND = {
    "pods": "Pod", "services": "Service", "endpoints": "Endpoints",
    "namespaces": "Namespace", "events": "Event", "nodes": "Node",
    "persistentvolumeclaims": "PersistentVolumeClaim",
    "configmaps": "ConfigMap", "secrets": "Secret",
    "deployments": "Deployment", "replicasets": "ReplicaSet",
    "statefulsets": "StatefulSet", "daemonsets": "DaemonSet",
    "ingresses": "Ingress", "jobs": "Job", "cronjobs": "CronJob",
    "storageclasses": "StorageClass",
}
RESOURCE_SHORT = {
    "pods": "po", "services": "svc", "namespaces": "ns",
    "nodes": "no", "configmaps": "cm",
    "persistentvolumeclaims": "pvc", "events": "ev", "endpoints": "ep",
    "deployments": "deploy", "replicasets": "rs",
    "statefulsets": "sts", "daemonsets": "ds",
    "ingresses": "ing",
}

# ---------- Routing ----------

PODS = [PAYMENT_API_POD, PAYMENT_WORKER_POD]
DEPLOYMENTS = [PAYMENT_API_DEPLOY, PAYMENT_WORKER_DEPLOY]
REPLICASETS = [PAYMENT_API_RS, PAYMENT_WORKER_RS]
SERVICES = [PAYMENT_API_SVC]
ENDPOINTS = [PAYMENT_API_ENDPOINTS]
PVCS = [PAYMENT_PVC]
INGRESSES = [PAYMENT_INGRESS]
NODES = [NODE_WORKER_1, NODE_WORKER_3]

CONFIGMAP_DEFAULT = {
    "apiVersion": "v1", "kind": "ConfigMap",
    "metadata": {"name": "cluster-config", "namespace": "default",
                 "uid": UID_BASE + "-cm1",
                 "creationTimestamp": "2026-08-15T08:00:00Z"},
    "data": {"environment": "production", "region": "us-east-1"},
}

# --- Values the reconciler keys off ------------------------------------------
# These are the exact broken values. A write that changes them away from these
# is what "remediation" means to the reconciler in cluster_state.py.

BROKEN_API_IMAGE = "registry.io/payments/api:1.4.2"
BROKEN_WORKER_MEMORY_LIMIT = "96Mi"
MIN_HEALTHY_WORKER_MEMORY_MI = 256
MISSING_INGRESS_CLASS = "nginx"
MISSING_BACKEND_SERVICE = "payment-frontend"
MISSING_STORAGE_CLASS = "standard"


def initial_collections():
    """Return a deep copy of the full broken-cluster state.

    Deep-copied so that (a) callers can mutate freely and (b) the Deployment
    pod templates stop aliasing the live Pod objects, which they do in the
    literals above.
    """
    return copy.deepcopy({
        ("", "pods"): [PAYMENT_API_POD, PAYMENT_WORKER_POD],
        ("", "services"): [PAYMENT_API_SVC],
        ("", "endpoints"): [PAYMENT_API_ENDPOINTS],
        ("", "persistentvolumeclaims"): [PAYMENT_PVC],
        ("", "configmaps"): [CONFIGMAP_DEFAULT],
        ("", "secrets"): [],
        ("", "events"): EVENTS,
        ("", "nodes"): [NODE_WORKER_1, NODE_WORKER_3],
        ("apps", "deployments"): [PAYMENT_API_DEPLOY, PAYMENT_WORKER_DEPLOY],
        ("apps", "replicasets"): [PAYMENT_API_RS, PAYMENT_WORKER_RS],
        ("apps", "statefulsets"): [],
        ("apps", "daemonsets"): [],
        ("networking.k8s.io", "ingresses"): [PAYMENT_INGRESS],
        ("networking.k8s.io", "ingressclasses"): [],
        ("storage.k8s.io", "storageclasses"): [],
        ("batch", "jobs"): [],
        ("batch", "cronjobs"): [],
    })
