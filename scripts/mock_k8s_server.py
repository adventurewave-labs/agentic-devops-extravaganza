"""
Mock Kubernetes API server for the Agentic DevOps demo.

Serves the deliberately-broken `payment-prod` cluster over the standard
Kubernetes REST API, with TLS, discovery, and full read/write verbs. Real
`kubectl` and the real `k8sgpt` Go binary talk to it exactly as they would to
a kube-apiserver — including writes.

This is a *simulator*, not a Kubernetes cluster. It is honest about that: it
never claims to be a real cluster, the reconciler's simulated behaviours are
enumerated in cluster_state.RECONCILER_RULES, and `kind/` in this repo runs
the same demo against an actual kind cluster when you want the real thing.

Endpoints beyond the Kubernetes API:
    GET  /_demo/health     compact broken/healthy summary (used by the demos)
    GET  /_demo/rules      the reconciler's rule table as JSON
    POST /_demo/reset      restore the pristine broken cluster

Usage:
    python scripts/mock_k8s_server.py [PORT] [--no-tls]
"""
import json
import os
import ssl
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cluster_fixtures as fx  # noqa: E402
import paths  # noqa: E402
from cluster_state import RECONCILER_RULES, ClusterState  # noqa: E402

STATE = ClusterState()

VERBS = ["create", "delete", "deletecollection", "get", "list", "patch",
         "update", "watch"]

# (group, version) -> {plural: (Kind, namespaced)}
GROUPS = {
    ("", "v1"): {
        "pods": ("Pod", True),
        "services": ("Service", True),
        "endpoints": ("Endpoints", True),
        "events": ("Event", True),
        "configmaps": ("ConfigMap", True),
        "secrets": ("Secret", True),
        "persistentvolumeclaims": ("PersistentVolumeClaim", True),
        "persistentvolumes": ("PersistentVolume", False),
        "namespaces": ("Namespace", False),
        "nodes": ("Node", False),
    },
    ("apps", "v1"): {
        "deployments": ("Deployment", True),
        "replicasets": ("ReplicaSet", True),
        "statefulsets": ("StatefulSet", True),
        "daemonsets": ("DaemonSet", True),
    },
    ("networking.k8s.io", "v1"): {
        "ingresses": ("Ingress", True),
        "ingressclasses": ("IngressClass", False),
    },
    ("storage.k8s.io", "v1"): {
        "storageclasses": ("StorageClass", False),
    },
    ("batch", "v1"): {
        "jobs": ("Job", True),
        "cronjobs": ("CronJob", True),
    },
    ("admissionregistration.k8s.io", "v1"): {
        "validatingwebhookconfigurations": ("ValidatingWebhookConfiguration", False),
        "mutatingwebhookconfigurations": ("MutatingWebhookConfiguration", False),
    },
}

SHORT_NAMES = {
    "pods": ["po"], "services": ["svc"], "namespaces": ["ns"], "nodes": ["no"],
    "configmaps": ["cm"], "persistentvolumeclaims": ["pvc"],
    "persistentvolumes": ["pv"], "events": ["ev"], "endpoints": ["ep"],
    "deployments": ["deploy"], "replicasets": ["rs"], "statefulsets": ["sts"],
    "daemonsets": ["ds"], "ingresses": ["ing"], "storageclasses": ["sc"],
    "cronjobs": ["cj"],
}

GROUP_VERSION = {g: v for (g, v) in GROUPS}


def log(msg):
    print(f"[mock-k8s] {msg}", file=sys.stderr, flush=True)


def _pb_string_field(number: int, value: str) -> bytes:
    """Encode one length-delimited protobuf string field."""
    payload = value.encode("utf-8")
    return bytes([(number << 3) | 2]) + _pb_varint(len(payload)) + payload


def _pb_message_field(number: int, payload: bytes) -> bytes:
    return bytes([(number << 3) | 2]) + _pb_varint(len(payload)) + payload


def _pb_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _openapi_v2_protobuf() -> bytes:
    """Hand-encode gnostic openapi_v2.Document{swagger, info{title, version}}.

    Document.swagger = 1, Document.info = 2;
    Info.title = 1, Info.version = 6.
    """
    info = _pb_string_field(1, "Kubernetes") + _pb_string_field(6, "v1.30.0")
    return _pb_string_field(1, "2.0") + _pb_message_field(2, info)


def _dig(obj, dotted):
    """Resolve a dotted field path against a nested dict."""
    node = obj
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def apply_field_selector(items, selector):
    """Filter a list by a Kubernetes fieldSelector expression.

    k8sgpt relies on this for real: its PVC and event lookups pass
    `involvedObject.name=<name>`, and an apiserver that silently ignored the
    selector would hand every analyzer the whole namespace's events and
    produce garbage findings. Supports `=`, `==` and `!=`, comma-joined.
    """
    if not selector:
        return items
    for clause in selector.split(","):
        clause = clause.strip()
        if not clause:
            continue
        if "!=" in clause:
            field, _, want = clause.partition("!=")
            items = [i for i in items if str(_dig(i, field.strip())) != want.strip()]
        elif "==" in clause:
            field, _, want = clause.partition("==")
            items = [i for i in items if str(_dig(i, field.strip())) == want.strip()]
        elif "=" in clause:
            field, _, want = clause.partition("=")
            items = [i for i in items if str(_dig(i, field.strip())) == want.strip()]
    return items


def apply_label_selector(items, selector):
    """Filter by an equality-based labelSelector (`k=v,k2=v2`)."""
    if not selector:
        return items
    for clause in selector.split(","):
        clause = clause.strip()
        if not clause or "=" not in clause:
            continue
        key, _, want = clause.partition("=")
        key, want = key.strip().rstrip("!"), want.strip()
        items = [i for i in items
                 if i.get("metadata", {}).get("labels", {}).get(key) == want]
    return items


def _plural_for(group, plural):
    for (g, _v), resources in GROUPS.items():
        if g == group and plural in resources:
            return resources[plural]
    return None


def _list_kind(group, plural):
    info = _plural_for(group, plural)
    return (info[0] if info else plural.capitalize()) + "List"


def _api_version(group):
    if group == "":
        return "v1"
    return f"{group}/{GROUP_VERSION.get(group, 'v1')}"


class MockK8sHandler(BaseHTTPRequestHandler):
    server_version = "MockKubeAPI/2.0"
    sys_version = "Python/3"
    protocol_version = "HTTP/1.1"
    timeout = 30
    query = {}

    # ---- plumbing ----------------------------------------------------------

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _status(self, code, reason, message):
        self._send_json({"kind": "Status", "apiVersion": "v1", "status": "Failure",
                         "code": code, "reason": reason, "message": message}, code)

    def _not_found(self, message):
        self._status(404, "NotFound", message)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def log_message(self, *args, **kwargs):
        pass

    # ---- verbs -------------------------------------------------------------

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._send_json({"kind": "Status", "apiVersion": "v1", "status": "Success"})

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        raw = self.path
        path = raw.split("?")[0].rstrip("/") or "/"
        self.query = parse_qs(urlparse(raw).query)
        log(f"{method} {path}")
        try:
            self._route(method, path)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # pragma: no cover - defensive
            log(f"ERROR {method} {path}: {type(exc).__name__}: {exc}")
            try:
                self._status(500, "InternalError", str(exc))
            except Exception:
                pass

    # ---- routing -----------------------------------------------------------

    def _route(self, method, path):
        if self._route_meta(method, path):
            return
        if self._route_discovery(method, path):
            return
        if self._route_namespaces(method, path):
            return
        return self._route_resource(method, path)

    def _route_meta(self, method, path):
        """Health, version, and the /_demo/* helpers."""
        if path in ("/readyz", "/livez", "/healthz", "/version"):
            if path == "/version":
                self._send_json({
                    "major": "1", "minor": "30", "gitVersion": "v1.30.0-mock",
                    "gitCommit": "mock", "platform": "linux/amd64",
                })
            else:
                self._send_json({"kind": "Status", "apiVersion": "v1",
                                 "status": "Success", "message": "ok",
                                 "reason": "ServerStatus"})
            return True
        if path == "/_demo/health":
            self._send_json(STATE.health_summary())
            return True
        if path == "/_demo/rules":
            self._send_json({"rules": [
                {"finding": f, "trigger": t, "effect": e}
                for f, t, e in RECONCILER_RULES]})
            return True
        if path == "/_demo/remediations":
            self._send_json({"applied": STATE.remediation_log})
            return True
        if path == "/_demo/reset":
            if method != "POST":
                self._status(405, "MethodNotAllowed", "POST /_demo/reset")
                return True
            STATE.reset()
            self._send_json({"reset": True, "state": STATE.health_summary()})
            return True
        if path.startswith("/openapi/"):
            # kubectl >=1.27 validates `apply`/`create -f` against OpenAPI v3.
            # It needs a real index at /openapi/v3 plus a fetchable (if
            # schema-less) document per group-version, otherwise every write
            # from a manifest dies with "error validating data". v2 is served
            # as protobuf by a real apiserver, so we simply refuse it.
            if path == "/openapi/v3":
                index = {}
                for (group, version) in GROUPS:
                    key = "api/v1" if group == "" else f"apis/{group}/{version}"
                    index[key] = {"serverRelativeURL": f"/openapi/v3/{key}?hash=mock"}
                self._send_json({"paths": index})
            elif path.startswith("/openapi/v3/"):
                self._send_json(self._openapi_doc(path))
            elif path == "/openapi/v2":
                self._send_openapi_v2()
            else:
                self._status(404, "NotFound", f"no openapi document at {path}")
            return True
        return False

    def _route_discovery(self, method, path):
        if path == "/api":
            self._send_json({"kind": "APIVersions", "versions": ["v1"],
                             "serverAddressByClientCIDRs": []})
            return True
        if path == "/apis":
            groups = []
            for (group, version) in GROUPS:
                if group == "":
                    continue
                gv = f"{group}/{version}"
                groups.append({
                    "name": group,
                    "versions": [{"groupVersion": gv, "version": version}],
                    "preferredVersion": {"groupVersion": gv, "version": version},
                })
            self._send_json({"kind": "APIGroupList", "apiVersion": "v1",
                             "groups": groups})
            return True
        if path == "/api/v1":
            self._send_json(self._resource_list("", "v1"))
            return True
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "apis":
            group, version = parts[1], parts[2]
            if (group, version) in GROUPS:
                self._send_json(self._resource_list(group, version))
                return True
        return False

    def _send_openapi_v2(self):
        """Serve a minimal, *valid* gnostic protobuf OpenAPI v2 document.

        kubectl's schema validator falls back to this endpoint whenever the
        v3 document doesn't resolve a GVK, and it parses the body as protobuf
        regardless of Content-Type — serving swagger JSON here is what
        produced "proto: cannot parse invalid wire-format data" and broke
        every `kubectl apply -f` against this mock. With no `definitions`,
        kubectl's LookupResource returns nil and validation is skipped, which
        is the correct behaviour for a server that doesn't publish schemas.
        """
        body = _openapi_v2_protobuf()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/com.github.proto-openapi.spec.v2+protobuf")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _openapi_doc(path):
        """A permissive but structurally real OpenAPI v3 document.

        kubectl resolves a manifest's schema by the
        `x-kubernetes-group-version-kind` extension. If it finds no schema for
        the GVK it falls back to the protobuf v2 endpoint, which a real
        apiserver serves and this mock does not — so `apply` would fail. We
        therefore emit one permissive object schema per GVK we serve, which is
        enough for validation to succeed without pretending to model the whole
        Kubernetes type system.
        """
        tail = path[len("/openapi/v3/"):]
        if tail.startswith("apis/"):
            bits = tail.split("/")
            group, version = bits[1], bits[2] if len(bits) > 2 else "v1"
        else:
            group, version = "", "v1"
        schemas = {}
        for plural, (kind, _ns) in GROUPS.get((group, version), {}).items():
            short = "core" if group == "" else group.split(".")[0]
            schemas[f"io.k8s.api.{short}.{version}.{kind}"] = {
                "type": "object",
                "description": f"{kind} (mock schema)",
                "x-kubernetes-group-version-kind": [
                    {"group": group, "kind": kind, "version": version}],
                "properties": {
                    "apiVersion": {"type": "string"},
                    "kind": {"type": "string"},
                    "metadata": {"type": "object"},
                },
            }
        return {
            "openapi": "3.0.0",
            "info": {"title": "Kubernetes", "version": "v1.30.0"},
            "paths": {},
            "components": {"schemas": schemas},
        }

    def _resource_list(self, group, version):
        gv = version if group == "" else f"{group}/{version}"
        resources = []
        for plural, (kind, namespaced) in GROUPS[(group, version)].items():
            resources.append({
                "name": plural,
                "singularName": kind.lower(),
                "namespaced": namespaced,
                "kind": kind,
                "verbs": VERBS,
                "shortNames": SHORT_NAMES.get(plural, []),
                "storageVersionHash": "mock",
            })
            # status subresource, so `kubectl patch --subresource=status` works
            resources.append({
                "name": f"{plural}/status",
                "singularName": "",
                "namespaced": namespaced,
                "kind": kind,
                "verbs": ["get", "patch", "update"],
            })
        return {"kind": "APIResourceList", "apiVersion": "v1",
                "groupVersion": gv, "resources": resources}

    def _route_namespaces(self, method, path):
        if path == "/api/v1/namespaces" and method == "GET":
            items = [self._namespace_obj(n) for n in fx.NAMESPACES]
            self._send_json({"apiVersion": "v1", "kind": "NamespaceList",
                             "metadata": {"resourceVersion": str(STATE.resource_version)},
                             "items": items})
            return True
        parts = path.strip("/").split("/")
        # /api/v1/namespaces/{ns}  -> only when there is no trailing resource
        if len(parts) == 4 and parts[:3] == ["api", "v1", "namespaces"]:
            ns = parts[3]
            if method == "GET":
                if ns in fx.NAMESPACES:
                    self._send_json(self._namespace_obj(ns))
                else:
                    self._not_found(f'namespaces "{ns}" not found')
                return True
        return False

    @staticmethod
    def _namespace_obj(name):
        return {"apiVersion": "v1", "kind": "Namespace",
                "metadata": {"name": name,
                             "uid": f"{fx.UID_BASE}-ns-{name}",
                             "creationTimestamp": "2026-08-15T08:00:00Z"},
                "status": {"phase": "Active"}}

    # ---- generic resource routing -----------------------------------------

    def _parse(self, path):
        """Parse a Kubernetes resource path.

        Returns (group, plural, namespace, name, subresource) or None.
        """
        parts = path.strip("/").split("/")
        if parts and parts[0] == "api" and len(parts) >= 2 and parts[1] == "v1":
            group, rest = "", parts[2:]
        elif parts and parts[0] == "apis" and len(parts) >= 3:
            group, rest = parts[1], parts[3:]
        else:
            return None
        namespace = None
        if rest[:1] == ["namespaces"] and len(rest) >= 3:
            namespace = rest[1]
            rest = rest[2:]
        if not rest:
            return None
        plural = rest[0]
        if _plural_for(group, plural) is None:
            return None
        name = rest[1] if len(rest) > 1 else None
        subresource = rest[2] if len(rest) > 2 else None
        return group, plural, namespace, name, subresource

    def _route_resource(self, method, path):
        parsed = self._parse(path)
        if parsed is None:
            log(f"UNHANDLED: {path}")
            return self._not_found(f"resource not found: {path}")
        group, plural, namespace, name, subresource = parsed

        if name is None:
            if method == "GET":
                items = STATE.list(group, plural, namespace)
                items = apply_field_selector(
                    items, (self.query.get("fieldSelector") or [""])[0])
                items = apply_label_selector(
                    items, (self.query.get("labelSelector") or [""])[0])
                return self._send_json({
                    "apiVersion": _api_version(group),
                    "kind": _list_kind(group, plural),
                    "metadata": {"resourceVersion": str(STATE.resource_version),
                                 "continue": ""},
                    "items": items,
                })
            if method == "POST":
                obj = self._read_body()
                created, code = STATE.create(group, plural, namespace, obj)
                if created is None:
                    return self._status(409, "AlreadyExists",
                                        f'{plural} "{obj.get("metadata", {}).get("name")}"'
                                        " already exists")
                return self._send_json(created, code)
            return self._status(405, "MethodNotAllowed",
                                f"{method} not allowed on collection {plural}")

        # single object (subresource writes fold onto the parent object, which
        # is what `kubectl patch --subresource=status` needs here)
        if method == "GET":
            obj = STATE.get(group, plural, namespace, name)
            if obj is None:
                return self._not_found(f'{plural} "{name}" not found')
            return self._send_json(obj)
        if method == "PATCH":
            body = self._read_body()
            ctype = self.headers.get("Content-Type", "")
            obj, code = STATE.patch(group, plural, namespace, name, body, ctype)
            if obj is None:
                return self._not_found(f'{plural} "{name}" not found')
            return self._send_json(obj, code)
        if method == "PUT":
            body = self._read_body()
            obj, code = STATE.replace(group, plural, namespace, name, body)
            if obj is None:
                return self._not_found(f'{plural} "{name}" not found')
            return self._send_json(obj, code)
        if method == "DELETE":
            obj, code = STATE.delete(group, plural, namespace, name)
            if obj is None:
                return self._not_found(f'{plural} "{name}" not found')
            return self._send_json({"kind": "Status", "apiVersion": "v1",
                                    "status": "Success",
                                    "details": {"name": name, "kind": plural}}, 200)
        return self._status(405, "MethodNotAllowed", f"{method} on {plural}/{name}")


class ResilientThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ssl.SSLError)):
            return
        log(f"connection error from {client_address}: {exc}")


def ensure_certs(cert_file: Path, key_file: Path) -> None:
    """Generate a self-signed cert on first boot if one isn't present.

    The repo deliberately does not commit a private key. Generating on demand
    is what makes `git clone && ./run.sh demo` work with no extra steps.
    """
    if cert_file.exists() and key_file.exists():
        return
    cert_file.parent.mkdir(parents=True, exist_ok=True)
    log(f"generating self-signed cert -> {cert_file}")
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key_file), "-out", str(cert_file), "-days", "3650",
        "-subj", "/CN=127.0.0.1",
        "-addext", "subjectAltName = IP:127.0.0.1,DNS:localhost",
    ], check=True, capture_output=True)
    os.chmod(key_file, 0o600)
    os.chmod(cert_file, 0o644)


def serve(port=None, use_tls=True, cert_file=None, key_file=None):
    port = port or paths.MOCK_K8S_PORT
    cert_file = Path(cert_file or paths.CERT_FILE)
    key_file = Path(key_file or paths.KEY_FILE)
    if use_tls:
        ensure_certs(cert_file, key_file)

    while True:
        httpd = None
        try:
            httpd = ResilientThreadingHTTPServer(("0.0.0.0", port), MockK8sHandler)
            if use_tls:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
                httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            scheme = "https" if use_tls else "http"
            log(f"serving broken payment-prod cluster on {scheme}://0.0.0.0:{port}")
            log(f"state: {json.dumps(STATE.health_summary())}")
            httpd.serve_forever()
        except KeyboardInterrupt:
            break
        except Exception as exc:
            log(f"server crashed: {type(exc).__name__}: {exc} - restarting in 2s")
        finally:
            if httpd is not None:
                try:
                    httpd.server_close()
                except Exception:
                    pass
        time.sleep(2)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    port_arg = int(args[0]) if args else paths.MOCK_K8S_PORT
    serve(port_arg, use_tls="--no-tls" not in sys.argv)
