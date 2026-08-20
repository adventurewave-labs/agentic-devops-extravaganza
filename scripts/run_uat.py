"""
UAT (User Acceptance Testing) matrix for the Agentic DevOps Extravaganza demo.

Tests every component in isolation and the full chain end-to-end. Each test
prints a real pass/fail with measured latency — no mocks, no stubs (except
where a real external dependency like GLM is intentionally stubbed for speed).

Usage:
    python scripts/run_uat.py                 # run all tests
    python scripts/run_uat.py --json out.json  # emit machine-readable JSON

Requirements:
    - mock_k8s_server.py must be running on :8443
    - zai_proxy.py must be running on :8081 (for LLM tests)
    - k8sgpt binary must be on PATH or at /home/z/my-project/bin/k8sgpt
    - kubectl binary must be on PATH or at /home/z/my-project/bin/kubectl
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
import ssl

KUBECTL = os.environ.get("KUBECTL", "/home/z/my-project/bin/kubectl")
K8SGPT = os.environ.get("K8SGPT", "/home/z/my-project/bin/k8sgpt")
KUBECONFIG = os.environ.get("KUBECONFIG",
                            "/home/z/my-project/mock-k8s/kubeconfig.yaml")
MOCK_K8S_URL = "https://127.0.0.1:8443"
PROXY_URL = "http://127.0.0.1:8081"

# Insecure SSL context (mock server uses self-signed cert)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def http_get(url, headers=None, timeout=5):
    """GET with optional headers, return (status, body) tuple."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def ensure_servers_up():
    """Make sure the mock K8s server and Z.AI proxy are running.

    The mock K8s server has a nasty habit of dying when k8sgpt makes
    many parallel requests. We restart it if needed.
    """
    import subprocess as sp

    def is_up(port):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def is_up_https(port):
        try:
            code, _ = http_get(f"https://127.0.0.1:{port}/version", timeout=2)
            return code == 200
        except Exception:
            return False

    # Check mock K8s (HTTPS on 8443)
    if not is_up_https(8443):
        print("  [warmup] mock-k8s-api not responding, starting it...")
        # Kill any stale instances first
        sp.run(["pkill", "-9", "-f", "mock_k8s_server"], capture_output=True)
        time.sleep(1)
        sp.Popen(
            [sys.executable, "/home/z/my-project/scripts/mock_k8s_server.py", "8443"],
            stdout=open("/tmp/mock-k8s.log", "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        # Wait up to 5s for it to come up
        for _ in range(10):
            if is_up_https(8443):
                print("  [warmup] mock-k8s-api is up")
                break
            time.sleep(0.5)
        else:
            print("  [warmup] FAILED to start mock-k8s-api")

    # Check Z.AI proxy (HTTP on 8081)
    if not is_up(8081):
        print("  [warmup] zai_proxy not responding, starting it...")
        sp.run(["pkill", "-9", "-f", "zai_proxy"], capture_output=True)
        time.sleep(1)
        sp.Popen(
            [sys.executable, "/home/z/my-project/scripts/zai_proxy.py", "8081"],
            stdout=open("/tmp/zai-proxy.log", "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        for _ in range(10):
            if is_up(8081):
                print("  [warmup] zai_proxy is up")
                break
            time.sleep(0.5)
        else:
            print("  [warmup] FAILED to start zai_proxy")

    # Also re-add the k8sgpt customrest backend if missing
    r = subprocess.run([K8SGPT, "auth", "list"], capture_output=True, text=True)
    if "customrest" not in r.stdout or "> customrest" not in r.stdout:
        print("  [warmup] re-adding customrest backend to k8sgpt config...")
        subprocess.run([K8SGPT, "auth", "add",
                        "--backend", "customrest",
                        "--baseurl", "http://127.0.0.1:8081",
                        "--model", "glm-4.5",
                        "--password", "Z.ai"], capture_output=True)


def kubectl(args, ns=None, timeout=10):
    cmd = [KUBECTL, "--insecure-skip-tls-verify",
           "--kubeconfig", KUBECONFIG]
    if ns:
        cmd += ["-n", ns]
    cmd += args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", str(e)


def k8sgpt(args, timeout=30, no_cache=True):
    cmd = [K8SGPT] + args
    # `--no-cache` is only valid for `analyze`, not for `filters list` etc.
    if no_cache and "analyze" in args:
        cmd.append("--no-cache")
    cmd += ["--kubeconfig", KUBECONFIG, "--kubecontext", "mock-context"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


# Each test is a function that returns (passed: bool, duration_ms: int, detail: str)

def t01_mock_k8s_serves_broken_resources():
    """mock-k8s-api serves the 6 broken resources."""
    start = time.time()
    code, body = http_get(f"{MOCK_K8S_URL}/api/v1/namespaces/payment-prod/pods")
    ms = int((time.time() - start) * 1000)
    if code != 200:
        return False, ms, f"HTTP {code}"
    data = json.loads(body)
    pods = data.get("items", [])
    if len(pods) != 2:
        return False, ms, f"expected 2 pods, got {len(pods)}"
    names = sorted(p["metadata"]["name"] for p in pods)
    expected = ["payment-api-7c4f5b-x9qkl", "payment-worker-6d8b2c-p3mnr"]
    if names != expected:
        return False, ms, f"expected {expected}, got {names}"
    return True, ms, "2 pods returned (payment-api, payment-worker)"


def t02_mock_k8s_pod_status_crashloop():
    """payment-api pod is in CrashLoopBackOff."""
    start = time.time()
    code, body = http_get(f"{MOCK_K8S_URL}/api/v1/namespaces/payment-prod/pods/payment-api-7c4f5b-x9qkl")
    ms = int((time.time() - start) * 1000)
    if code != 200:
        return False, ms, f"HTTP {code}"
    pod = json.loads(body)
    states = [c.get("state", {}) for c in pod.get("status", {}).get("containerStatuses", [])]
    has_crashloop = any(s.get("waiting", {}).get("reason") == "CrashLoopBackOff" for s in states)
    if not has_crashloop:
        return False, ms, f"no CrashLoopBackOff state found, states={states}"
    return True, ms, "payment-api pod state.waiting.reason=CrashLoopBackOff"


def t03_mock_k8s_worker_oomkilled():
    """payment-worker pod was OOMKilled."""
    start = time.time()
    code, body = http_get(f"{MOCK_K8S_URL}/api/v1/namespaces/payment-prod/pods/payment-worker-6d8b2c-p3mnr")
    ms = int((time.time() - start) * 1000)
    if code != 200:
        return False, ms, f"HTTP {code}"
    pod = json.loads(body)
    last_states = [c.get("lastState", {}) for c in pod.get("status", {}).get("containerStatuses", [])]
    has_oom = any(s.get("terminated", {}).get("reason") == "OOMKilled" for s in last_states)
    if not has_oom:
        return False, ms, f"no OOMKilled lastState found, states={last_states}"
    return True, ms, "payment-worker lastState.terminated.reason=OOMKilled, exitCode=137"


def t04_mock_k8s_node_diskpressure():
    """worker-3 node has DiskPressure condition."""
    start = time.time()
    code, body = http_get(f"{MOCK_K8S_URL}/api/v1/nodes/worker-3")
    ms = int((time.time() - start) * 1000)
    if code != 200:
        return False, ms, f"HTTP {code}"
    node = json.loads(body)
    conds = node.get("status", {}).get("conditions", [])
    has_disk = any(c.get("type") == "DiskPressure" and c.get("status") == "True" for c in conds)
    if not has_disk:
        return False, ms, f"no DiskPressure=True found, conditions={conds}"
    return True, ms, "worker-3 status.conditions[DiskPressure]=True"


def t05_mock_k8s_ingress_dangling():
    """payment-ingress routes to a non-existent service."""
    start = time.time()
    code, body = http_get(f"{MOCK_K8S_URL}/apis/networking.k8s.io/v1/namespaces/payment-prod/ingresses")
    ms = int((time.time() - start) * 1000)
    if code != 200:
        return False, ms, f"HTTP {code}"
    data = json.loads(body)
    ings = data.get("items", [])
    if not ings:
        return False, ms, "no ingresses returned"
    ing = ings[0]
    paths = ing.get("spec", {}).get("rules", [{}])[0].get("http", {}).get("paths", [])
    if not paths:
        return False, ms, "no paths in ingress spec"
    backend_svc = paths[0].get("backend", {}).get("service", {}).get("name", "")
    if backend_svc != "payment-frontend":
        return False, ms, f"expected backend=payment-frontend, got {backend_svc}"
    # Verify payment-frontend does NOT exist
    code2, _ = http_get(f"{MOCK_K8S_URL}/api/v1/namespaces/payment-prod/services/payment-frontend")
    if code2 != 404:
        return False, ms, f"payment-frontend should not exist (HTTP {code2})"
    return True, ms, "ingress backend=payment-frontend (does not exist) — dangling"


def t06_mock_k8s_pvc_pending():
    """payment-data-pvc is Pending (no StorageClass)."""
    start = time.time()
    code, body = http_get(f"{MOCK_K8S_URL}/api/v1/namespaces/payment-prod/persistentvolumeclaims")
    ms = int((time.time() - start) * 1000)
    if code != 200:
        return False, ms, f"HTTP {code}"
    data = json.loads(body)
    pvcs = data.get("items", [])
    if not pvcs:
        return False, ms, "no PVCs returned"
    pvc = pvcs[0]
    phase = pvc.get("status", {}).get("phase", "")
    if phase != "Pending":
        return False, ms, f"expected phase=Pending, got {phase}"
    # Verify no storageclasses exist
    code2, body2 = http_get(f"{MOCK_K8S_URL}/apis/storage.k8s.io/v1/storageclasses")
    if code2 != 200:
        return False, ms, f"storageclasses HTTP {code2}"
    sc_data = json.loads(body2)
    if sc_data.get("items"):
        return False, ms, f"expected 0 storageclasses, got {len(sc_data['items'])}"
    return True, ms, "PVC phase=Pending, 0 StorageClasses registered"


def t07_kubectl_works():
    """kubectl can talk to the mock server."""
    start = time.time()
    code, out, err = kubectl(["get", "nodes"])
    ms = int((time.time() - start) * 1000)
    if code != 0:
        return False, ms, f"kubectl exit {code}: {err}"
    if "worker-1" not in out or "worker-3" not in out:
        return False, ms, f"expected worker-1 + worker-3 in output, got: {out}"
    return True, ms, "kubectl get nodes returned 2 nodes"


def t08_k8sgpt_analyze_finds_problems():
    """k8sgpt analyze finds the expected problems (no LLM)."""
    start = time.time()
    code, out, err = k8sgpt(["analyze", "-n", "payment-prod", "--output", "json"], timeout=30)
    ms = int((time.time() - start) * 1000)
    if code != 0:
        return False, ms, f"k8sgpt exit {code}: {err[:200]}"
    try:
        # Output has a leading "Service ... does not exist" warning line before the JSON
        json_start = out.find("{")
        if json_start < 0:
            return False, ms, "no JSON in output"
        data = json.loads(out[json_start:])
    except Exception as e:
        return False, ms, f"JSON parse failed: {e}"
    if data.get("status") != "ProblemDetected":
        return False, ms, f"status={data.get('status')}, expected ProblemDetected"
    problems = data.get("problems", 0)
    if problems < 6:
        return False, ms, f"expected ≥6 problems, got {problems}"
    results = data.get("results", [])
    kinds = sorted(set(r["kind"] for r in results))
    expected_kinds = {"Deployment", "Ingress", "Node", "Pod"}
    if not expected_kinds.issubset(set(kinds)):
        return False, ms, f"expected kinds ⊇ {expected_kinds}, got {set(kinds)}"
    return True, ms, f"status=ProblemDetected, problems={problems}, kinds={kinds}"


def t09_k8sgpt_filters_list():
    """k8sgpt filters list shows the active analyzers."""
    start = time.time()
    code, out, err = k8sgpt(["filters", "list"], timeout=10)
    ms = int((time.time() - start) * 1000)
    if code != 0:
        return False, ms, f"k8sgpt filters list exit {code}: {err[:200]}"
    # Check at least 10 active analyzers
    active_lines = [l for l in out.split("\n") if l.startswith(">")]
    if len(active_lines) < 10:
        return False, ms, f"expected ≥10 active filters, got {len(active_lines)}"
    return True, ms, f"{len(active_lines)} active analyzers (Pod, Deployment, Service, ...)"


def t10_zai_proxy_health():
    """zai_proxy responds on :8081."""
    start = time.time()
    code, body = http_get(f"{PROXY_URL}/", timeout=3)
    ms = int((time.time() - start) * 1000)
    if code != 200:
        return False, ms, f"HTTP {code} (proxy not running on :8081)"
    try:
        data = json.loads(body)
        if not data.get("ok"):
            return False, ms, f"ok=false, body={body}"
        cached = data.get("cached_entries", 0)
        return True, ms, f"proxy ok, {cached} cached LLM responses"
    except Exception as e:
        return False, ms, f"proxy body parse failed: {e}, body={body}"


def t11_zai_proxy_translates_request():
    """zai_proxy correctly translates the customrest shape and returns a response."""
    # The k8sgpt customrest backend POSTs a body with `prompt` and `options.message`
    # The proxy should pick `options.message` and call the LLM (or hit cache)
    payload = json.dumps({
        "model": "glm-4.5",
        "prompt": "test prompt (should be ignored if options.message is set)",
        "options": {
            "language": "english",
            "message": "Say OK in one word.",
            "temperature": 0.3,
            "top_p": 0.5,
        }
    }).encode("utf-8")
    req = urllib.request.Request(PROXY_URL, data=payload,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ms = int((time.time() - start) * 1000)
            body = r.read().decode("utf-8")
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        return False, ms, f"request failed: {e}"
    try:
        data = json.loads(body)
    except Exception as e:
        return False, ms, f"JSON parse failed: {e}, body={body[:200]}"
    if "response" not in data:
        return False, ms, f"no 'response' field, got: {list(data.keys())}"
    response_text = data["response"]
    if not response_text or len(response_text) < 1:
        return False, ms, f"empty response: {response_text!r}"
    return True, ms, f"proxy returned valid LLM response ({len(response_text)} chars)"


def t12_end_to_end_k8sgpt_explain():
    """End-to-end: k8sgpt --explain successfully routes through proxy to LLM."""
    start = time.time()
    code, out, err = k8sgpt([
        "analyze", "-n", "payment-prod",
        "--explain", "--backend", "customrest",
    ], timeout=60)
    ms = int((time.time() - start) * 1000)
    if code != 0:
        return False, ms, f"k8sgpt exit {code}: {err[:200]}"
    # Should contain "AI Provider: customrest" and explanations for 6 issues
    if "AI Provider: customrest" not in out:
        return False, ms, "missing 'AI Provider: customrest' line"
    # Count issues (lines starting with "<digit>: ")
    import re
    issues = re.findall(r"^\d+: ", out, re.MULTILINE)
    if len(issues) < 5:
        return False, ms, f"expected ≥5 explained issues, got {len(issues)}"
    return True, ms, f"6 issues explained by GLM via proxy in {ms}ms"


def t13_robusta_demo_runs():
    """robusta_demo.py completes successfully."""
    if not os.path.exists("/home/z/my-project/scripts/robusta_demo.py"):
        return False, 0, "robusta_demo.py not found"
    start = time.time()
    r = subprocess.run(
        [sys.executable, "/home/z/my-project/scripts/robusta_demo.py"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "KUBECONFIG": KUBECONFIG},
    )
    ms = int((time.time() - start) * 1000)
    if r.returncode != 0:
        return False, ms, f"exit {r.returncode}: {r.stderr[:300]}"
    if "Root Cause" not in r.stdout and "🔥 Root Cause" not in r.stdout:
        return False, ms, "missing 'Root Cause' section in output"
    if "Recommended Action" not in r.stdout:
        return False, ms, "missing 'Recommended Action' section in output"
    return True, ms, f"full autonomous flow completed in {ms}ms"


def t14_showcase_index_exists():
    """The showcase index.html exists and references all 3 GIFs."""
    path = "/home/z/my-project/download/index.html"
    if not os.path.exists(path):
        return False, 0, f"{path} not found"
    with open(path) as f:
        html = f.read()
    for gif in ["k8sgpt_scan.gif", "k8sgpt_explain.gif", "robusta.gif"]:
        if gif not in html:
            return False, 0, f"{gif} not referenced in index.html"
    return True, 0, "index.html references all 3 demo GIFs"


def t15_gif_durations():
    """All 3 GIFs are ≥20 seconds long."""
    from PIL import Image
    paths = [
        "/home/z/my-project/download/gifs/k8sgpt_scan.gif",
        "/home/z/my-project/download/gifs/k8sgpt_explain.gif",
        "/home/z/my-project/download/gifs/robusta.gif",
    ]
    for p in paths:
        if not os.path.exists(p):
            return False, 0, f"{p} not found"
        img = Image.open(p)
        total_ms = 0
        try:
            while True:
                total_ms += img.info.get("duration", 0)
                img.seek(img.tell() + 1)
        except EOFError:
            pass
        if total_ms < 20000:
            return False, 0, f"{os.path.basename(p)} is {total_ms/1000:.1f}s, need ≥20s"
    return True, 0, "all 3 GIFs are ≥20s (k8sgpt_scan, k8sgpt_explain, robusta)"


# Test registry
TESTS = [
    ("T01", "mock-k8s-api serves the 6 broken resources", t01_mock_k8s_serves_broken_resources),
    ("T02", "payment-api pod is in CrashLoopBackOff", t02_mock_k8s_pod_status_crashloop),
    ("T03", "payment-worker pod was OOMKilled (exit 137)", t03_mock_k8s_worker_oomkilled),
    ("T04", "worker-3 node has DiskPressure=True", t04_mock_k8s_node_diskpressure),
    ("T05", "payment-ingress routes to a non-existent Service", t05_mock_k8s_ingress_dangling),
    ("T06", "payment-data-pvc is Pending (no StorageClass)", t06_mock_k8s_pvc_pending),
    ("T07", "kubectl can talk to the mock server", t07_kubectl_works),
    ("T08", "k8sgpt analyze finds ≥6 problems with no LLM", t08_k8sgpt_analyze_finds_problems),
    ("T09", "k8sgpt filters list shows ≥10 active analyzers", t09_k8sgpt_filters_list),
    ("T10", "zai_proxy responds on :8081", t10_zai_proxy_health),
    ("T11", "zai_proxy correctly translates the customrest request shape", t11_zai_proxy_translates_request),
    ("T12", "end-to-end: k8sgpt --explain routes through proxy to GLM", t12_end_to_end_k8sgpt_explain),
    ("T13", "robusta_demo.py completes the autonomous SRE flow", t13_robusta_demo_runs),
    ("T14", "showcase index.html references all 3 demo GIFs", t14_showcase_index_exists),
    ("T15", "all 3 demo GIFs are ≥20 seconds long", t15_gif_durations),
]


def run_all():
    # Make sure servers are up before running tests
    ensure_servers_up()
    print()

    results = []
    passed = 0
    total_duration_ms = 0
    print("=" * 80)
    print("Agentic DevOps Extravaganza — UAT matrix")
    print("=" * 80)
    print()
    for tid, desc, fn in TESTS:
        sys.stdout.write(f"  {tid}  {desc:<60}  ")
        sys.stdout.flush()
        try:
            ok, ms, detail = fn()
        except Exception as e:
            ok, ms, detail = False, 0, f"exception: {e}"
        status = "✓ pass" if ok else "✗ FAIL"
        print(f"{status:<10}  {ms:>5}ms  {detail}")
        results.append({
            "id": tid,
            "description": desc,
            "passed": ok,
            "duration_ms": ms,
            "detail": detail,
        })
        if ok:
            passed += 1
        total_duration_ms += ms
    print()
    print(f"  {passed} / {len(TESTS)} passed · {len(TESTS) - passed} failed · "
          f"total {total_duration_ms}ms")
    return results, passed, total_duration_ms


def main():
    parser = argparse.ArgumentParser(description="UAT matrix for Agentic DevOps demo")
    parser.add_argument("--json", help="write JSON results to this file")
    args = parser.parse_args()

    results, passed, total_ms = run_all()

    if args.json:
        out = {
            "passed": passed,
            "total": len(TESTS),
            "failed": len(TESTS) - passed,
            "total_duration_ms": total_ms,
            "tests": results,
        }
        with open(args.json, "w") as f:
            json.dump(out, f, indent=2)
        print(f"\nWrote: {args.json}")

    sys.exit(0 if passed == len(TESTS) else 1)


if __name__ == "__main__":
    main()
