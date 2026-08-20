"""
Mock Kubernetes API Server for the K8sGPT / Robusta demo.

Serves a realistic "broken cluster" state via the standard Kubernetes REST API.
Tools that speak Kubernetes (kubectl, k8sgpt, robusta) hit this server exactly
as if it were a real kube-apiserver. The state we return is intentionally broken
in several realistic ways, so k8sgpt's analyzers can find real problems.

The cluster "story":

  Namespace: payment-prod
    - Pod payment-api-7c4f5b-x9qkl     CrashLoopBackOff (image does not exist)
    - Pod payment-worker-6d8b2c-p3mnr   OOMKilled (limits too low)
    - Deployment payment-api            replica mismatch (0/1 ready)
    - Deployment payment-worker          replica mismatch (0/1 ready)
    - Service payment-api-svc           no endpoints (selector mismatch)
    - Service payment-db-svc           ClusterIP only, OK
    - PVC payment-data-pvc              Pending (no storage class)
    - Secret payment-api-secret         MISSING (referenced by deployment)
    - Ingress payment-ingress           backend service does not exist

  Namespace: default
    - Node worker-3                     DiskPressure condition
    - Node worker-1                     Ready
    - ConfigMap cluster-config          OK
"""
import json
import os
import ssl
import threading
import time
import http.server
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


def list_wrap(items, kind):
    api_ver = "v1"
    if items:
        api_ver = items[0].get("apiVersion", "v1")
    return {
        "apiVersion": api_ver,
        "kind": kind,
        "metadata": {"resourceVersion": "184523", "continue": ""},
        "items": items,
    }


def apps_list(items, kind):
    return {"apiVersion": "apps/v1", "kind": kind,
            "metadata": {"resourceVersion": "184523"},
            "items": items}


def net_list(items, kind):
    return {"apiVersion": "networking.k8s.io/v1", "kind": kind,
            "metadata": {"resourceVersion": "184523"},
            "items": items}


def storage_list(items, kind):
    return {"apiVersion": "storage.k8s.io/v1", "kind": kind,
            "metadata": {"resourceVersion": "184523"},
            "items": items}


def filter_by_namespace(items, ns):
    if ns is None or ns == "":
        return items
    return [i for i in items if i.get("metadata", {}).get("namespace") == ns]


class MockK8sHandler(BaseHTTPRequestHandler):
    server_version = "MockKubeAPI/1.0"
    sys_version = "Python/3.12"
    protocol_version = "HTTP/1.1"  # support keep-alive
    timeout = 30  # per-request timeout

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Date", "Tue, 20 Aug 2026 04:00:00 GMT")
        # Explicitly set Connection: close to avoid keep-alive weirdness
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_status(self):
        # /readyz / livez / healthz
        self._send_json({"kind": "Status", "apiVersion": "v1",
                         "status": "Success", "message": "ok",
                         "reason": "ServerStatus"})

    def do_GET(self):
        path = self.path.split("?")[0]
        # strip api group prefix
        log(f"GET {path}")
        try:
            self._route(path)
        except BrokenPipeError:
            pass
        except ConnectionResetError:
            pass
        except Exception as e:
            log(f"ERROR routing {path}: {e}")
            try:
                self._send_json({"kind": "Status", "apiVersion": "v1",
                                 "status": "Failure", "code": 500, "message": str(e)}, 500)
            except Exception:
                pass

    def do_HEAD(self):
        self._send_json({"kind": "Status", "apiVersion": "v1", "status": "Success"}, 200)

    def _route(self, path):
        # Health endpoints
        if path in ("/readyz", "/livez", "/healthz", "/version"):
            return self._send_status()

        if path == "/api":
            return self._send_json({"kind": "APIVersions",
                                   "versions": ["v1"],
                                   "serverAddressByClientCIDRs": []})

        if path == "/api/v1":
            # Some core resources are cluster-scoped (nodes, namespaces, persistentvolumes)
            cluster_scoped = {"nodes", "namespaces", "persistentvolumes"}
            res = []
            for r in API_V1_RESOURCES:
                entry = {
                    "name": r,
                    "singularName": r[:-1] if r.endswith("s") else r,
                    "namespaced": r not in cluster_scoped,
                    "kind": RESOURCE_KIND.get(r, r[:-1].capitalize()),
                    "verbs": ["get", "list", "watch"],
                }
                if RESOURCE_SHORT.get(r):
                    entry["shortNames"] = [RESOURCE_SHORT[r]]
                res.append(entry)
            return self._send_json({"kind": "APIResourceList", "apiVersion": "v1",
                                    "groupVersion": "v1", "resources": res})

        if path == "/apis":
            return self._send_json({"kind": "APIGroupList", "groups": [
                {"name": "apps", "versions": [{"groupVersion": "apps/v1", "version": "v1"}],
                 "preferredVersion": {"groupVersion": "apps/v1", "version": "v1"}},
                {"name": "networking.k8s.io",
                 "versions": [{"groupVersion": "networking.k8s.io/v1", "version": "v1"}],
                 "preferredVersion": {"groupVersion": "networking.k8s.io/v1", "version": "v1"}},
                {"name": "storage.k8s.io",
                 "versions": [{"groupVersion": "storage.k8s.io/v1", "version": "v1"}],
                 "preferredVersion": {"groupVersion": "storage.k8s.io/v1", "version": "v1"}},
                {"name": "batch",
                 "versions": [{"groupVersion": "batch/v1", "version": "v1"}],
                 "preferredVersion": {"groupVersion": "batch/v1", "version": "v1"}},
                {"name": "admissionregistration.k8s.io",
                 "versions": [{"groupVersion": "admissionregistration.k8s.io/v1", "version": "v1"}],
                 "preferredVersion": {"groupVersion": "admissionregistration.k8s.io/v1", "version": "v1"}},
            ]})

        # /apis/apps/v1
        if path == "/apis/apps/v1":
            res = []
            for r in APPS_V1_RESOURCES:
                entry = {"name": r, "namespaced": True,
                         "kind": RESOURCE_KIND.get(r, r[:-1].capitalize()),
                         "verbs": ["get", "list", "watch"]}
                if RESOURCE_SHORT.get(r):
                    entry["shortNames"] = [RESOURCE_SHORT[r]]
                res.append(entry)
            return self._send_json({"kind": "APIResourceList", "apiVersion": "v1",
                                    "groupVersion": "apps/v1", "resources": res})

        # /apis/networking.k8s.io/v1
        if path == "/apis/networking.k8s.io/v1":
            res = []
            for r in NETWORKING_V1_RESOURCES:
                entry = {"name": r, "namespaced": True,
                         "kind": RESOURCE_KIND.get(r, r[:-1].capitalize()),
                         "verbs": ["get", "list", "watch"]}
                if RESOURCE_SHORT.get(r):
                    entry["shortNames"] = [RESOURCE_SHORT[r]]
                res.append(entry)
            return self._send_json({"kind": "APIResourceList", "apiVersion": "v1",
                                    "groupVersion": "networking.k8s.io/v1", "resources": res})

        # /apis/storage.k8s.io/v1
        if path == "/apis/storage.k8s.io/v1":
            res = [{"name": "storageclasses", "namespaced": False,
                     "kind": "StorageClass", "verbs": ["get", "list", "watch"]}]
            return self._send_json({"kind": "APIResourceList", "apiVersion": "v1",
                                    "groupVersion": "storage.k8s.io/v1", "resources": res})

        # === Core v1 collections ===
        # /api/v1/namespaces
        if path == "/api/v1/namespaces":
            return self._send_json(NAMESPACES_LIST)
        # /api/v1/namespaces/{ns}
        if path.startswith("/api/v1/namespaces/") and path.count("/") == 3:
            ns = path.split("/")[-1]
            if ns in NAMESPACES:
                return self._send_json({"apiVersion": "v1", "kind": "Namespace",
                                        "metadata": {"name": ns, "uid": UID_BASE + "-ns-x",
                                                     "creationTimestamp": "2026-08-15T08:00:00Z"},
                                        "status": {"phase": "Active"}})
            return self._send_404(f"namespaces \"{ns}\" not found")

        # /api/v1/namespaces/{ns}/pods
        if path.startswith("/api/v1/namespaces/") and path.endswith("/pods"):
            ns = path.split("/")[4]
            items = filter_by_namespace(PODS, ns)
            return self._send_json(list_wrap(items, "PodList"))
        # /api/v1/pods  (cluster-wide)
        if path == "/api/v1/pods":
            return self._send_json(list_wrap(PODS, "PodList"))
        # /api/v1/namespaces/{ns}/pods/{name}
        if path.startswith("/api/v1/namespaces/") and "/pods/" in path:
            ns = path.split("/")[4]
            name = path.split("/")[-1]
            for p in PODS:
                if p["metadata"]["namespace"] == ns and p["metadata"]["name"] == name:
                    return self._send_json(p)
            return self._send_404(f"pods \"{name}\" not found")

        # services
        if path == "/api/v1/services":
            return self._send_json(list_wrap(SERVICES, "ServiceList"))
        if path.startswith("/api/v1/namespaces/") and path.endswith("/services"):
            ns = path.split("/")[4]
            items = filter_by_namespace(SERVICES, ns)
            return self._send_json(list_wrap(items, "ServiceList"))

        # endpoints
        if path == "/api/v1/endpoints":
            return self._send_json(list_wrap(ENDPOINTS, "EndpointsList"))
        if path.startswith("/api/v1/namespaces/") and path.endswith("/endpoints"):
            ns = path.split("/")[4]
            items = filter_by_namespace(ENDPOINTS, ns)
            return self._send_json(list_wrap(items, "EndpointsList"))

        # events
        if path == "/api/v1/events":
            return self._send_json(list_wrap(EVENTS, "EventList"))
        if path.startswith("/api/v1/namespaces/") and path.endswith("/events"):
            ns = path.split("/")[4]
            items = [e for e in EVENTS
                     if e.get("involvedObject", {}).get("namespace") == ns]
            return self._send_json(list_wrap(items, "EventList"))

        # nodes
        if path == "/api/v1/nodes":
            return self._send_json(list_wrap(NODES, "NodeList"))
        if path.startswith("/api/v1/nodes/"):
            name = path.split("/")[-1]
            for n in NODES:
                if n["metadata"]["name"] == name:
                    return self._send_json(n)
            return self._send_404(f"nodes \"{name}\" not found")

        # pvcs
        if path == "/api/v1/persistentvolumeclaims":
            return self._send_json(list_wrap(PVCS, "PersistentVolumeClaimList"))
        if path.startswith("/api/v1/namespaces/") and path.endswith("/persistentvolumeclaims"):
            ns = path.split("/")[4]
            items = filter_by_namespace(PVCS, ns)
            return self._send_json(list_wrap(items, "PersistentVolumeClaimList"))

        # configmaps
        if path == "/api/v1/configmaps":
            return self._send_json(list_wrap([CONFIGMAP_DEFAULT], "ConfigMapList"))
        if path.startswith("/api/v1/namespaces/") and path.endswith("/configmaps"):
            ns = path.split("/")[4]
            items = [CONFIGMAP_DEFAULT] if ns == "default" else []
            return self._send_json(list_wrap(items, "ConfigMapList"))

        # secrets (empty)
        if path == "/api/v1/secrets" or (
                path.startswith("/api/v1/namespaces/") and path.endswith("/secrets")):
            return self._send_json(list_wrap([], "SecretList"))

        # === apps/v1 ===
        if path == "/apis/apps/v1/deployments":
            return self._send_json(apps_list(DEPLOYMENTS, "DeploymentList"))
        if path.startswith("/apis/apps/v1/namespaces/") and path.endswith("/deployments"):
            ns = path.split("/")[5]
            items = filter_by_namespace(DEPLOYMENTS, ns)
            return self._send_json(apps_list(items, "DeploymentList"))

        if path == "/apis/apps/v1/replicasets":
            return self._send_json(apps_list(REPLICASETS, "ReplicaSetList"))
        if path.startswith("/apis/apps/v1/namespaces/") and path.endswith("/replicasets"):
            ns = path.split("/")[5]
            items = filter_by_namespace(REPLICASETS, ns)
            return self._send_json(apps_list(items, "ReplicaSetList"))

        # statefulsets / daemonsets (empty)
        if path == "/apis/apps/v1/statefulsets" or path.endswith("/statefulsets"):
            return self._send_json(apps_list([], "StatefulSetList"))
        if path == "/apis/apps/v1/daemonsets" or path.endswith("/daemonsets"):
            return self._send_json(apps_list([], "DaemonSetList"))

        # === networking ===
        if path == "/apis/networking.k8s.io/v1/ingresses":
            return self._send_json(net_list(INGRESSES, "IngressList"))
        if path.startswith("/apis/networking.k8s.io/v1/namespaces/") and path.endswith("/ingresses"):
            ns = path.split("/")[5]
            items = filter_by_namespace(INGRESSES, ns)
            return self._send_json(net_list(items, "IngressList"))

        # === storage ===
        if path == "/apis/storage.k8s.io/v1/storageclasses":
            return self._send_json(STORAGE_CLASS_LIST)

        # === batch (empty) ===
        if path == "/apis/batch/v1":
            res = []
            for r in BATCH_V1_RESOURCES:
                entry = {"name": r, "namespaced": True,
                         "kind": RESOURCE_KIND.get(r, r[:-1].capitalize()),
                         "verbs": ["get", "list", "watch"]}
                res.append(entry)
            return self._send_json({"kind": "APIResourceList", "apiVersion": "v1",
                                    "groupVersion": "batch/v1", "resources": res})
        if path == "/apis/batch/v1/jobs" or path == "/apis/batch/v1/cronjobs" or "/jobs" in path or "/cronjobs" in path:
            return self._send_json({"apiVersion": "batch/v1", "kind": "JobList",
                                    "metadata": {"resourceVersion": "184523"}, "items": []})

        # === admissionregistration.k8s.io (empty) ===
        if path == "/apis/admissionregistration.k8s.io/v1":
            res = [
                {"name": "validatingwebhookconfigurations", "namespaced": False,
                 "kind": "ValidatingWebhookConfiguration", "verbs": ["get", "list", "watch"]},
                {"name": "mutatingwebhookconfigurations", "namespaced": False,
                 "kind": "MutatingWebhookConfiguration", "verbs": ["get", "list", "watch"]},
            ]
            return self._send_json({"kind": "APIResourceList", "apiVersion": "v1",
                                    "groupVersion": "admissionregistration.k8s.io/v1", "resources": res})
        if path == "/apis/admissionregistration.k8s.io/v1/validatingwebhookconfigurations":
            return self._send_json({"apiVersion": "admissionregistration.k8s.io/v1",
                                    "kind": "ValidatingWebhookConfigurationList",
                                    "metadata": {"resourceVersion": "184523"}, "items": []})
        if path == "/apis/admissionregistration.k8s.io/v1/mutatingwebhookconfigurations":
            return self._send_json({"apiVersion": "admissionregistration.k8s.io/v1",
                                    "kind": "MutatingWebhookConfigurationList",
                                    "metadata": {"resourceVersion": "184523"}, "items": []})

        # === IngressClass (empty so the "nginx" class truly doesn't exist) ===
        if path == "/apis/networking.k8s.io/v1/ingressclasses":
            return self._send_json({"apiVersion": "networking.k8s.io/v1",
                                    "kind": "IngressClassList",
                                    "metadata": {"resourceVersion": "184523"}, "items": []})

        # OpenAPI / swagger
        if path == "/openapi/v2":
            return self._send_json({"swagger": "2.0",
                                    "info": {"title": "Kubernetes", "version": "v1.30.0"},
                                    "paths": {}, "definitions": {}})

        # default
        log(f"UNHANDLED: {path}")
        return self._send_404(f"resource not found: {path}")

    def _send_404(self, msg):
        self._send_json({"kind": "Status", "apiVersion": "v1", "status": "Failure",
                         "code": 404, "reason": "NotFound", "message": msg}, 404)

    def do_POST(self):
        # k8sgpt/robusta don't POST, but kubectl does for some operations.
        self._send_json({"kind": "Status", "apiVersion": "v1", "status": "Success",
                         "code": 201, "message": "created"}, 201)

    def log_message(self, *args, **kwargs):
        # silence default logging
        pass


def log(msg):
    print(f"[mock-k8s] {msg}", flush=True)


class ResilientThreadingHTTPServer(ThreadingHTTPServer):
    """HTTPServer that survives broken connections and SSL errors."""
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 256

    def handle_error(self, request, client_address):
        # swallow SSL errors and broken pipes silently
        import sys
        exc_type, exc_value, _ = sys.exc_info()
        if exc_type is None:
            return
        # common errors we don't care about
        ignorable = (ConnectionResetError, BrokenPipeError, ssl.SSLError,
                     ConnectionAbortedError, TimeoutError)
        if isinstance(exc_value, ignorable):
            return
        log(f"handle_error: {exc_type.__name__}: {exc_value}")


def serve(port=8443, use_tls=True, certfile=None, keyfile=None):
    # Auto-restart loop: if the server crashes (e.g. due to a broken
    # TLS handshake from a misbehaving client), restart it within 1 second.
    # This is what keeps the demo alive across multiple k8sgpt invocations.
    while True:
        try:
            httpd = ResilientThreadingHTTPServer(("127.0.0.1", port), MockK8sHandler)
            if use_tls and certfile and keyfile:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)
                httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            log(f"Mock Kubernetes API listening on {'https' if use_tls else 'http'}://127.0.0.1:{port}")
            httpd.serve_forever()
        except KeyboardInterrupt:
            break
        except Exception as e:
            log(f"Server crashed: {type(e).__name__}: {e} - restarting in 1s")
            import time
            time.sleep(1)


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8443
    use_tls = "--no-tls" not in sys.argv
    certfile = os.environ.get("K8S_MOCK_CERT", "/home/z/my-project/mock-k8s/cert.pem")
    keyfile = os.environ.get("K8S_MOCK_KEY", "/home/z/my-project/mock-k8s/key.pem")
    if not use_tls:
        certfile = keyfile = None
    serve(port, use_tls, certfile, keyfile)
