"""
A minimal reference implementation of the alert -> context -> LLM -> card loop
that Robusta runs, in about 250 lines.

WHAT THIS IS
    A readable, dependency-free implementation of the same four steps a
    Robusta playbook performs when a Prometheus alert fires:
        1. accept an Alertmanager webhook payload
        2. gather related cluster context from the Kubernetes API
        3. ask an LLM for a root-cause analysis grounded in that context
        4. render (and optionally post) a Slack finding card

WHAT THIS IS NOT
    This is NOT Robusta. Robusta is a real open-source project with a real
    Helm chart, playbook engine, sinks, and enrichers:
        https://github.com/robusta-dev/robusta
    This file re-implements the *shape* of its flow so the loop is legible in
    one screen of Python. To watch actual Robusta do this against an actual
    Kubernetes cluster, use the `kind/` directory in this repo:
        make kind-up && make kind-robusta && make kind-fire-alert

    An earlier version of this script printed "Jira ticket PAY-1247 created"
    and "acknowledged in PagerDuty". It had no Jira or PagerDuty client and
    never had. Those lines are gone. The only integration here is Slack, it
    is opt-in via SLACK_WEBHOOK_URL, and when it is unset the script says the
    card was rendered but not posted.

Usage:
    python scripts/alert_triage_agent.py [--alert path/to/alert.json]
                                         [--post-slack] [--json]
"""
import argparse
import json
import os
import subprocess
import sys
import textwrap
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402
from llm_proxy import call_llm  # noqa: E402

DEFAULT_ALERT = paths.ROOT / "alerts" / "payment-api-high-error-rate.json"


def kubectl(args, namespace=None, timeout=15):
    """Run a real kubectl command against whatever cluster KUBECONFIG names."""
    cmd = [paths.kubectl_bin(), "--kubeconfig", str(paths.KUBECONFIG)]
    context = os.environ.get("KUBE_CONTEXT")
    if context:
        cmd += ["--context", context]
    if os.environ.get("KUBE_INSECURE", "1") == "1":
        cmd += ["--insecure-skip-tls-verify"]
    if namespace:
        cmd += ["-n", namespace]
    cmd += args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return f"[kubectl unavailable: {exc}]"
    return (result.stdout or result.stderr).strip()


def load_alert(path):
    with open(path) as handle:
        return json.load(handle)


def gather_context(alert):
    """Query the cluster for the objects the alert points at.

    Every value here is a live API read — nothing is pre-baked. If the pod has
    already been remediated, the context reflects that and the LLM says so.
    """
    first = alert["alerts"][0]
    labels = first["labels"]
    namespace = labels.get("namespace", "default")
    pod = labels.get("pod", "")
    service = labels.get("service", "")
    return {
        "namespace": namespace,
        "pod_name": pod,
        "pod_status": kubectl(
            ["get", "pod", pod, "-o", "jsonpath={.status}"], namespace),
        "pod_events": kubectl(
            ["get", "events", "--field-selector",
             f"involvedObject.name={pod}"], namespace),
        "deployment_status": kubectl(
            ["get", "deploy", service, "-o", "jsonpath={.status}"], namespace),
        "endpoints": kubectl(
            ["get", "endpoints", "-o", "wide"], namespace),
        "recent_events": kubectl(
            ["get", "events", "--sort-by=.lastTimestamp"], namespace),
        "nodes": kubectl(["get", "nodes", "-o", "wide"]),
    }


def build_prompt(alert, context):
    first = alert["alerts"][0]
    labels = first["labels"]
    annotations = first["annotations"]
    return f"""You are an SRE agent investigating a Prometheus alert. Ground every
statement in the cluster state provided; if the state does not support a
claim, say so rather than guessing.

ALERT:     {labels.get('alertname')}
SEVERITY:  {labels.get('severity')}
NAMESPACE: {labels.get('namespace')}
SERVICE:   {labels.get('service')}
POD:       {labels.get('pod')}
STARTED:   {first.get('startsAt')}

SUMMARY:     {annotations.get('summary')}
DESCRIPTION: {annotations.get('description')}

CLUSTER STATE READ AT INVESTIGATION TIME:
- Pod status: {context['pod_status'][:600]}
- Pod events: {context['pod_events'][:600]}
- Deployment status: {context['deployment_status'][:400]}
- Endpoints: {context['endpoints'][:300]}
- Recent namespace events: {context['recent_events'][:600]}

Produce markdown, under 2000 characters, in exactly this structure:

## Root Cause
<1-2 sentences, grounded in the state above>

## Evidence
<bullets quoting the specific fields that support it>

## Impact
<what is affected and how badly>

## Recommended Action
<the exact kubectl commands to run>
"""


def slack_blocks(alert, analysis):
    labels = alert["alerts"][0]["labels"]
    return {
        "text": f"Incident: {labels.get('alertname')}",
        "blocks": [
            {"type": "header",
             "text": {"type": "plain_text",
                      "text": f"🚨 {labels.get('alertname')}"}},
            {"type": "context", "elements": [
                {"type": "mrkdwn",
                 "text": (f"*severity* {labels.get('severity')}  |  "
                          f"*ns* {labels.get('namespace')}  |  "
                          f"*service* {labels.get('service')}")}]},
            {"type": "section",
             "text": {"type": "mrkdwn", "text": analysis[:2900]}},
        ],
    }


def post_to_slack(payload):
    """Post the card to Slack. Opt-in: requires SLACK_WEBHOOK_URL."""
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        return False, "SLACK_WEBHOOK_URL not set - card rendered locally only"
    request = urllib.request.Request(
        webhook, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            return True, f"posted to Slack (HTTP {resp.status})"
    except Exception as exc:
        return False, f"Slack post failed: {exc}"


def box(text_lines, width=76):
    out = ["┌" + "─" * (width + 2) + "┐"]
    for line in text_lines:
        for wrapped in (textwrap.wrap(line, width) or [""]):
            out.append(f"│ {wrapped:<{width}} │")
    out.append("└" + "─" * (width + 2) + "┘")
    return "\n".join(out)


def run(alert_path, post_slack, as_json):
    alert = load_alert(alert_path)
    labels = alert["alerts"][0]["labels"]

    print("=" * 80)
    print("ALERT TRIAGE AGENT  ·  alert → cluster context → LLM → Slack card")
    print("A reference implementation of Robusta's flow. Not Robusta itself.")
    print("=" * 80)

    print(f"\n[1/4] Received Alertmanager webhook: {labels.get('alertname')}")
    print(f"      severity={labels.get('severity')} "
          f"namespace={labels.get('namespace')} pod={labels.get('pod')}")

    print("\n[2/4] Reading cluster state via the Kubernetes API")
    started = time.time()
    context = gather_context(alert)
    for key in ("pod_status", "pod_events", "deployment_status",
                "recent_events", "nodes"):
        value = context[key]
        state = "empty" if not value else f"{len(value)} chars"
        print(f"      ✓ {key:<20} {state}")

    print("\n[3/4] Asking the LLM for a root-cause analysis")
    prompt = build_prompt(alert, context)
    analysis, source = call_llm(prompt, temperature=0.3, top_p=0.7)
    elapsed = time.time() - started
    print(f"      source={source}  chars={len(analysis)}  elapsed={elapsed:.1f}s")
    if source in ("replay-miss", "error"):
        print("      NOTE: no live model answered. See README > LLM backends.")

    print("\n[4/4] Slack finding card")
    header = [
        f"🚨 {labels.get('alertname')}   severity={labels.get('severity')}",
        f"namespace={labels.get('namespace')}  service={labels.get('service')}"
        f"  pod={labels.get('pod')}",
        "",
    ]
    print(box(header + analysis.split("\n")))

    posted, detail = (False, "not attempted")
    if post_slack:
        posted, detail = post_to_slack(slack_blocks(alert, analysis))
    else:
        detail = "--post-slack not passed; card rendered locally only"
    print(f"\nSlack: {detail}")

    paths.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    result = {
        "alert": labels.get("alertname"),
        "llm_source": source,
        "elapsed_seconds": round(elapsed, 2),
        "analysis": analysis,
        "slack_posted": posted,
        "slack_detail": detail,
    }
    out_path = paths.OUTPUT_DIR / "alert_triage.json"
    with open(out_path, "w") as handle:
        json.dump(result, handle, indent=2)
    print(f"Wrote {out_path}")

    if as_json:
        print(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alert", default=str(DEFAULT_ALERT),
                        help="Alertmanager webhook payload JSON")
    parser.add_argument("--post-slack", action="store_true",
                        help="actually POST to $SLACK_WEBHOOK_URL")
    parser.add_argument("--json", action="store_true",
                        help="also print the result as JSON")
    args = parser.parse_args()
    run(args.alert, args.post_slack, args.json)


if __name__ == "__main__":
    main()
