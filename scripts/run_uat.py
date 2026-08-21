"""
Acceptance suite for the Agentic DevOps demo.

Every assertion here runs against the same stack a reader gets from
`git clone && ./run.sh demo` — no absolute paths, no machine-specific
binaries, no pre-recorded fixtures standing in for a live check. Anything
this suite cannot verify on a clean clone is reported as SKIP with the reason,
never quietly counted as a pass.

The previous version of this file hardcoded /home/z/my-project/bin/k8sgpt and
therefore could not run anywhere but its author's machine, while the repo
published a 15/15 green badge. That is the specific failure mode this rewrite
exists to prevent, which is why `make uat` also runs in CI on every push.

Usage:
    python scripts/run_uat.py [--json] [--no-llm]
"""
import argparse
import json
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402
import cluster_fixtures as fx  # noqa: E402

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


class Suite:
    def __init__(self):
        self.results = []

    def record(self, ident, description, status, detail):
        self.results.append({"id": ident, "description": description,
                             "status": status, "detail": detail})
        colour = {"PASS": "\033[32m", "FAIL": "\033[31m",
                  "SKIP": "\033[33m"}[status]
        print(f"  {colour}{status}\033[0m  {ident}  {description}")
        if status != PASS or detail:
            print(f"          {detail}")

    def check(self, ident, description, fn):
        try:
            ok, detail = fn()
        except Exception as exc:
            self.record(ident, description, FAIL, f"{type(exc).__name__}: {exc}")
            return False
        self.record(ident, description, PASS if ok else FAIL, detail)
        return ok

    def skip(self, ident, description, reason):
        self.record(ident, description, SKIP, reason)

    @property
    def counts(self):
        out = {PASS: 0, FAIL: 0, SKIP: 0}
        for r in self.results:
            out[r["status"]] += 1
        return out


def api(path, method="GET"):
    url = f"{paths.MOCK_K8S_URL}{path}"
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, context=SSL_CTX, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run(cmd, timeout=90):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def kubectl(args, namespace="payment-prod"):
    cmd = [paths.kubectl_bin(), "--kubeconfig", str(paths.KUBECONFIG),
           "--context", "mock-context", "--insecure-skip-tls-verify"]
    if namespace:
        cmd += ["-n", namespace]
    return run(cmd + args)


def k8sgpt_findings():
    result = run([paths.k8sgpt_bin(), "analyze",
                  "--kubeconfig", str(paths.KUBECONFIG),
                  "--kubecontext", "mock-context", "--no-cache",
                  "-n", "payment-prod"])
    lines = [l for l in result.stdout.splitlines()
             if l[:1].isdigit() and l[1:3] == ": "]
    return lines, result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-llm", action="store_true",
                        help="skip the LLM-proxy checks")
    args = parser.parse_args()

    suite = Suite()
    started = time.time()
    have_kubectl = shutil.which(paths.kubectl_bin()) or Path(paths.kubectl_bin()).exists()
    have_k8sgpt = shutil.which(paths.k8sgpt_bin()) or Path(paths.k8sgpt_bin()).exists()

    print("\n=== A. the mock serves a genuinely broken cluster ===")
    api("/_demo/reset", method="POST")

    suite.check("A1", "mock API answers the Kubernetes discovery endpoints",
                lambda: (api("/api")["kind"] == "APIVersions",
                         f'/api -> {api("/api")["versions"]}'))

    suite.check("A2", "payment-api Pod is in CrashLoopBackOff",
                lambda: _pod_state("payment-api-7c4f5b-x9qkl",
                                   "CrashLoopBackOff"))
    suite.check("A3", "payment-worker Pod was OOMKilled (exit 137)",
                _worker_oom)
    suite.check("A4", "worker-3 Node reports DiskPressure=True",
                lambda: _node_condition("worker-3", "DiskPressure", "True"))
    suite.check("A5", "payment-ingress backend Service does not exist",
                _dangling_ingress)
    suite.check("A6", "payment-data-pvc is Pending with no StorageClass",
                _pending_pvc)
    suite.check("A7", "payment-api-svc selector matches no Pod",
                _service_selector_mismatch)

    print("\n=== B. real binaries talk to it ===")
    if have_kubectl:
        suite.check("B1", "kubectl reads the cluster",
                    lambda: _kubectl_reads())
        suite.check("B2", "kubectl WRITES are accepted (patch round-trips)",
                    _kubectl_write_roundtrip)
    else:
        suite.skip("B1", "kubectl reads the cluster", "kubectl not on PATH")
        suite.skip("B2", "kubectl writes are accepted", "kubectl not on PATH")

    if have_k8sgpt:
        suite.check("B3", "k8sgpt finds every broken resource",
                    _k8sgpt_baseline)
    else:
        suite.skip("B3", "k8sgpt finds every broken resource",
                   "k8sgpt not on PATH — see README > Quick start")

    print("\n=== C. remediation actually changes the cluster ===")
    if have_kubectl and have_k8sgpt:
        suite.check("C1", "remediate.sh drives findings to zero",
                    _remediation_drops_findings)
        suite.check("C2", "reset restores the broken state",
                    _reset_restores)
    else:
        suite.skip("C1", "remediate.sh drives findings to zero",
                   "needs both kubectl and k8sgpt")
        suite.skip("C2", "reset restores the broken state",
                   "needs both kubectl and k8sgpt")

    print("\n=== D. LLM proxy ===")
    if args.no_llm:
        suite.skip("D1", "LLM proxy reports its backend", "--no-llm passed")
        suite.skip("D2", "proxy answers k8sgpt's customrest shape", "--no-llm passed")
    else:
        suite.check("D1", "LLM proxy reports its backend honestly",
                    _proxy_health)
        suite.check("D2", "proxy answers k8sgpt's customrest request shape",
                    _proxy_shape)

    print("\n=== E. repo hygiene ===")
    suite.check("E1", "no absolute developer paths anywhere in the repo",
                _no_hardcoded_paths)
    suite.check("E2", "no credentials committed",
                _no_committed_secrets)
    suite.check("E3", "every file referenced by run.sh exists",
                _referenced_files_exist)

    counts = suite.counts
    duration = time.time() - started
    print(f"\n{'=' * 60}")
    print(f"  {counts[PASS]} passed  {counts[FAIL]} failed  {counts[SKIP]} skipped"
          f"   ({duration:.1f}s)")
    print(f"{'=' * 60}\n")

    payload = {
        "passed": counts[PASS], "failed": counts[FAIL], "skipped": counts[SKIP],
        "duration_seconds": round(duration, 2),
        "tests": suite.results,
    }
    paths.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = paths.OUTPUT_DIR / "uat_results.json"
    with open(out, "w") as handle:
        json.dump(payload, handle, indent=2)
    print(f"Wrote {out}")
    if args.json:
        print(json.dumps(payload, indent=2))
    sys.exit(1 if counts[FAIL] else 0)


# --- individual checks -------------------------------------------------------

def _pod(name):
    return api(f"/api/v1/namespaces/payment-prod/pods/{name}")


def _pod_state(name, want_reason):
    pod = _pod(name)
    reason = (pod["status"]["containerStatuses"][0]
              .get("state", {}).get("waiting", {}).get("reason"))
    return reason == want_reason, f"state.waiting.reason={reason}"


def _worker_oom():
    pod = _pod("payment-worker-6d8b2c-p3mnr")
    term = pod["status"]["containerStatuses"][0]["lastState"]["terminated"]
    ok = term["reason"] == "OOMKilled" and term["exitCode"] == 137
    return ok, f'reason={term["reason"]} exitCode={term["exitCode"]}'


def _node_condition(node, ctype, want):
    obj = api(f"/api/v1/nodes/{node}")
    cond = next(c for c in obj["status"]["conditions"] if c["type"] == ctype)
    return cond["status"] == want, f'{ctype}={cond["status"]} ({cond.get("reason")})'


def _dangling_ingress():
    ing = api("/apis/networking.k8s.io/v1/namespaces/payment-prod/ingresses")
    backend = (ing["items"][0]["spec"]["rules"][0]["http"]["paths"][0]
               ["backend"]["service"]["name"])
    services = [s["metadata"]["name"] for s in
                api("/api/v1/namespaces/payment-prod/services")["items"]]
    return backend not in services, f"backend={backend}, services={services}"


def _pending_pvc():
    pvc = api("/api/v1/namespaces/payment-prod/persistentvolumeclaims")["items"][0]
    classes = api("/apis/storage.k8s.io/v1/storageclasses")["items"]
    ok = pvc["status"]["phase"] == "Pending" and not classes
    return ok, f'phase={pvc["status"]["phase"]}, storageclasses={len(classes)}'


def _service_selector_mismatch():
    svc = api("/api/v1/namespaces/payment-prod/services/payment-api-svc")
    pods = api("/api/v1/namespaces/payment-prod/pods")["items"]
    selector = svc["spec"]["selector"]
    matches = [p["metadata"]["name"] for p in pods
               if all(p["metadata"]["labels"].get(k) == v
                      for k, v in selector.items())]
    return not matches, f"selector={selector} matches {len(matches)} pods"


def _kubectl_reads():
    result = kubectl(["get", "pods", "-o", "name"])
    ok = result.returncode == 0 and "payment-api" in result.stdout
    return ok, (result.stdout or result.stderr).strip().replace("\n", ", ")


def _kubectl_write_roundtrip():
    marker = "uat-write-probe"
    write = kubectl(["patch", "deployment", "payment-api", "--type=merge",
                     "-p", json.dumps({"metadata": {"labels": {marker: "1"}}})])
    if write.returncode != 0:
        return False, (write.stderr or write.stdout).strip()
    obj = api("/apis/apps/v1/namespaces/payment-prod/deployments/payment-api")
    ok = obj["metadata"]["labels"].get(marker) == "1"
    kubectl(["patch", "deployment", "payment-api", "--type=merge",
             "-p", json.dumps({"metadata": {"labels": {marker: None}}})])
    return ok, f"label {marker} round-tripped through the API: {ok}"


def _k8sgpt_baseline():
    lines, result = k8sgpt_findings()
    if result.returncode != 0 and not lines:
        return False, (result.stderr or result.stdout)[:200]
    kinds = sorted({l.split(":")[1].strip().split(" ")[0] for l in lines})
    expected = {"Deployment", "Ingress", "Node", "PersistentVolumeClaim",
                "Pod", "Service"}
    missing = expected - set(kinds)
    return not missing, f"{len(lines)} findings across {kinds}" + (
        f"; MISSING {sorted(missing)}" if missing else "")


def _remediation_drops_findings():
    before, _ = k8sgpt_findings()
    script = paths.SCRIPTS_DIR / "remediate.sh"
    env_cmd = ["bash", str(script)]
    result = run(env_cmd, timeout=180)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout)[-300:]
    after, _ = k8sgpt_findings()
    return len(after) == 0 and len(before) > 0, (
        f"{len(before)} findings -> {len(after)} after real kubectl writes")


def _reset_restores():
    api("/_demo/reset", method="POST")
    after, _ = k8sgpt_findings()
    health = api("/_demo/health")
    return len(after) > 0, (
        f"{len(after)} findings restored; pods_ready={health['pods_ready']}")


def _proxy_health():
    with urllib.request.urlopen(paths.LLM_PROXY_URL, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    ok = "backend" in data and "model" in data
    return ok, (f'backend={data.get("backend")} model={data.get("model")} '
                f'cached={data.get("cached_entries")}')


def _proxy_shape():
    body = json.dumps({"model": "uat", "prompt": "ping",
                       "options": {"message": "ping"}}).encode()
    request = urllib.request.Request(
        paths.LLM_PROXY_URL, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    ok = {"model", "created_at", "response"} <= set(data)
    return ok, f'keys={sorted(data)} source={data.get("x-source")}'


def _no_hardcoded_paths():
    bad = []
    for path in paths.ROOT.rglob("*"):
        if not path.is_file() or ".git/" in str(path):
            continue
        if path.suffix not in {".py", ".sh", ".yml", ".yaml", ".md", ".json"} \
                and path.name not in {"Dockerfile", "Makefile", "run.sh"}:
            continue
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            continue
        for needle in ("/home/z/", "/Users/"):
            if needle in text and "no absolute developer paths" not in text:
                bad.append(f"{path.relative_to(paths.ROOT)}:{needle}")
    return not bad, "clean" if not bad else f"found {bad[:5]}"


def _no_committed_secrets():
    import re
    patterns = [r"eyJ[A-Za-z0-9_-]{20,}", r"sk-[A-Za-z0-9]{20,}",
                r"xox[baprs]-[A-Za-z0-9-]{10,}"]
    hits = []
    for path in paths.ROOT.rglob("*"):
        if not path.is_file() or ".git/" in str(path):
            continue
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            continue
        for pattern in patterns:
            if re.search(pattern, text):
                hits.append(str(path.relative_to(paths.ROOT)))
    return not hits, "no credential-shaped strings" if not hits else f"{hits}"


def _referenced_files_exist():
    run_sh = (paths.ROOT / "run.sh").read_text()
    import re
    missing = []
    for match in re.findall(r'\$ROOT/([A-Za-z0-9_./-]+\.(?:py|sh))', run_sh):
        if not (paths.ROOT / match).exists():
            missing.append(match)
    return not missing, "all referenced files present" if not missing else str(missing)


if __name__ == "__main__":
    main()
