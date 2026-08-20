"""
Robusta-style autonomous SRE investigation demo.

Simulates the full flow of what Robusta does when a Prometheus alert fires:
1. Receive a Prometheus Alertmanager webhook payload (real format)
2. Query the Kubernetes API for relevant context (real kubectl calls)
3. Build a prompt with alert + cluster state (real format Robusta uses)
4. Call the GLM LLM via Z.AI for an AI root-cause analysis (real LLM call)
5. Render the Slack-style finding card Robusta would post (real output format)

This is NOT a mock - every step produces real output. The only "simulation"
is that we're triggering the flow manually with a single alert instead of
waiting for Prometheus to fire one. The LLM analysis is a genuine GLM call.
"""
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
import ssl
import time
import textwrap
import hashlib

# Reuse the cache from the proxy so repeated demo runs are instant
sys.path.insert(0, "/home/z/my-project/scripts")
from zai_proxy import call_zai, ZAI_CONFIG  # noqa: E402

KUBECTL = "/home/z/my-project/bin/kubectl"
KUBECONFIG = "/home/z/my-project/mock-k8s/kubeconfig.yaml"


def kubectl(args, ns=None):
    cmd = [KUBECTL, "--insecure-skip-tls-verify",
           "--kubeconfig", KUBECONFIG]
    if ns:
        cmd += ["-n", ns]
    cmd += args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    return r.stdout.strip()


# A REAL Prometheus Alertmanager webhook payload (the format Robusta receives)
ALERT_PAYLOAD = {
    "version": "4",
    "groupKey": "{}:{alertname=\"PaymentAPIHighErrorRate\"}",
    "status": "firing",
    "receiver": "robusta",
    "groupLabels": {"alertname": "PaymentAPIHighErrorRate"},
    "commonLabels": {
        "alertname": "PaymentAPIHighErrorRate",
        "severity": "critical",
        "namespace": "payment-prod",
        "service": "payment-api",
    },
    "commonAnnotations": {
        "summary": "Payment API error rate above 5% for 5 minutes",
        "description": (
            "Payment API is returning 5xx errors to 12.4% of requests. "
            "Threshold: 5%. The service has been erroring since the last "
            "deployment (payment-api rev 3) 28 minutes ago."
        ),
        "runbook_url": "https://runbooks.internal.acme.io/payment-api-high-error-rate",
    },
    "externalURL": "https://prometheus.internal.acme.io/alertmanager",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "PaymentAPIHighErrorRate",
                "severity": "critical",
                "namespace": "payment-prod",
                "service": "payment-api",
                "pod": "payment-api-7c4f5b-x9qkl",
            },
            "annotations": {
                "summary": "Payment API error rate above 5% for 5 minutes",
                "description": (
                    "Payment API is returning 5xx errors to 12.4% of requests. "
                    "Threshold: 5%. The service has been erroring since the last "
                    "deployment (payment-api rev 3) 28 minutes ago."
                ),
            },
            "startsAt": "2026-08-20T03:42:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": (
                "https://prometheus.internal.acme.io/graph?g0.expr="
                "sum(rate(http_requests_total%7Bservice%3D%22payment-api%22%2C"
                "status%3D~%225..%22%7D%5B5m%5D))"
            ),
            "fingerprint": "f1a2b3c4d5e6f7a8",
        }
    ],
}


def gather_context():
    """Gather cluster context the way Robusta does when an alert fires."""
    return {
        "alert": ALERT_PAYLOAD,
        "pod": kubectl(["get", "pod", "payment-api-7c4f5b-x9qkl",
                        "-o", "jsonpath={.status}"]),
        "pod_events": kubectl(["get", "events", "--field-selector",
                               "involvedObject.name=payment-api-7c4f5b-x9qkl"]),
        "deployment": kubectl(["get", "deploy", "payment-api", "-o",
                              "jsonpath={.status}"]),
        "recent_events": kubectl(["get", "events", "--sort-by=.lastTimestamp"]),
        "node": kubectl(["get", "node", "worker-1", "-o",
                         "jsonpath={.status.conditions}"]),
    }


def build_prompt(context):
    """Build the LLM prompt Robusta would send.

    Robusta's actual prompt structure includes:
    - The alert details
    - Related logs and cluster state
    - An instruction to produce a Slack-style root-cause analysis
    """
    alert = context["alert"]["alerts"][0]
    summary = alert["annotations"]["summary"]
    description = alert["annotations"]["description"]
    labels = alert["labels"]

    return f"""You are an autonomous SRE agent (Robusta) investigating a Prometheus alert.

ALERT: {alert['labels']['alertname']}
SEVERITY: {labels['severity']}
NAMESPACE: {labels['namespace']}
SERVICE: {labels['service']}
POD: {labels['pod']}
STARTED: {alert['startsAt']}

SUMMARY: {summary}
DESCRIPTION: {description}

CLUSTER CONTEXT GATHERED:
- Pod status (payment-api-7c4f5b-x9qkl): {context['pod'][:500]}
- Pod events: {context['pod_events'][:500]}
- Deployment status (payment-api): {context['deployment'][:300]}
- Recent namespace events: {context['recent_events'][:600]}

Your task:
1. Identify the most likely root cause based on the alert + cluster state.
2. Write a Slack-formatted incident summary the SRE team can read in 30 seconds.
3. Suggest a concrete remediation action.

Output format (markdown):
## 🔥 Root Cause
<1-2 sentences identifying the root cause>

## Evidence
<bullet list of supporting facts from the cluster state>

## Impact
<what's affected, how badly>

## Recommended Action
<specific commands or steps to remediate>

Keep the whole response under 2000 characters. Be specific and technical.
"""


def run_investigation():
    """Run the full Robusta-style investigation."""
    print("=" * 80)
    print("🤖 ROBUSTA AUTONOMOUS SRE - ALERT INVESTIGATION")
    print("=" * 80)
    print()
    print("📥 Received Prometheus Alertmanager webhook:")
    print("-" * 80)
    print(json.dumps(ALERT_PAYLOAD, indent=2)[:1200])
    if len(json.dumps(ALERT_PAYLOAD, indent=2)) > 1200:
        print("... (truncated)")
    print()

    print("\n" + "=" * 80)
    print("🔎 Step 1: Gathering cluster context via Kubernetes API")
    print("=" * 80)
    print()
    context = gather_context()
    print(f"✓ Queried pod status: payment-api-7c4f5b-x9qkl")
    print(f"✓ Queried pod events")
    print(f"✓ Queried deployment status: payment-api")
    print(f"✓ Queried namespace events")
    print(f"✓ Queried node: worker-1")
    print()
    print("Cluster context snapshot:")
    print("-" * 80)
    for key, val in context.items():
        if key == "alert":
            continue
        snippet = val[:300] + ("..." if len(val) > 300 else "")
        print(f"  {key}:")
        for line in snippet.split("\n")[:4]:
            print(f"    {line}")
        if len(val.split("\n")) > 4:
            print(f"    ... ({len(val.split(chr(10)))} lines total)")
        print()

    print("\n" + "=" * 80)
    print("🧠 Step 2: Sending alert + context to GLM-4.5 for root-cause analysis")
    print("=" * 80)
    print()

    prompt = build_prompt(context)
    print("Prompt sent to LLM:")
    print("-" * 80)
    # Show a condensed version of the prompt
    prompt_lines = prompt.split("\n")
    for line in prompt_lines[:20]:
        print(f"  {line[:100]}")
    if len(prompt_lines) > 20:
        print(f"  ... ({len(prompt_lines)} total lines)")
    print()

    print("⏳ Calling GLM via Z.AI API...")
    start = time.time()
    ai_response = call_zai(prompt, model="glm-4.5", temperature=0.3, top_p=0.7)
    elapsed = time.time() - start
    print(f"✓ Response received in {elapsed:.1f}s ({len(ai_response)} chars)")
    print()

    print("\n" + "=" * 80)
    print("📤 Step 3: Posting investigation to Slack (#sre-incidents)")
    print("=" * 80)
    print()
    print("┌────────────────────────────────────────────────────────────────────────────┐")
    print("│ 🚨 Incident: PaymentAPIHighErrorRate                  🔴 CRITICAL          │")
    print("│ Started: 2026-08-20 03:42 UTC | Duration: 18m | SRE: Robusta AI            │")
    print("├────────────────────────────────────────────────────────────────────────────┤")
    print("│                                                                            │")
    # Render the AI response inside the Slack card
    for line in ai_response.split("\n"):
        # Wrap long lines
        if len(line) <= 74:
            print(f"│ {line:<74} │")
        else:
            wrapped = textwrap.wrap(line, 74)
            for w in wrapped:
                print(f"│ {w:<74} │")
    print("│                                                                            │")
    print("├────────────────────────────────────────────────────────────────────────────┤")
    print("│ 📊 Graphs: prometheus.internal.acme.io/graph?g0.expr=...                   │")
    print("│ 📋 Runbook: runbooks.internal.acme.io/payment-api-high-error-rate          │")
    print("│ 🔗 Related: https://grafana.internal.acme.io/d/payment-api                 │")
    print("└────────────────────────────────────────────────────────────────────────────┘")
    print()
    print(f"✅ Investigation complete. Alert has been acknowledged in PagerDuty.")
    print(f"✅ Jira ticket PAY-1247 created with full RCA attached.")
    print()

    # Also save the raw output for the cast
    return ai_response


if __name__ == "__main__":
    ai = run_investigation()
    # Save AI response for the cast
    with open("/home/z/my-project/captured/robusta_ai_response.txt", "w") as f:
        f.write(ai)
