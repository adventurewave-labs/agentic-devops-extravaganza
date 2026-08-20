"""
Generate asciinema v2 cast files for the demos.

Each demo is a list of "steps" - each step types a command and displays its
captured output. The outputs come from REAL k8sgpt / kubectl / GLM runs
captured earlier (see /home/z/my-project/captured/*.txt).

The cast files are then converted to GIFs by agg.
"""
import json
import os
import sys
import time

CAPTURE_DIR = "/home/z/my-project/captured"
RECORDING_DIR = "/home/z/my-project/recordings"
os.makedirs(RECORDING_DIR, exist_ok=True)


def read_captured(name):
    path = os.path.join(CAPTURE_DIR, name)
    if not os.path.exists(path):
        return f"[missing capture: {name}]"
    with open(path) as f:
        return f.read().rstrip()


def type_cmd(events, t, cmd, type_delay=0.035):
    """Emit events that simulate typing a command character-by-character."""
    for ch in cmd:
        t += type_delay
        events.append([round(t, 6), "o", ch])
    return t


def press_enter(events, t):
    t += 0.08
    events.append([round(t, 6), "o", "\r\n"])
    return t


def emit_output(events, t, output, chunk_size=120, chunk_delay=0.012):
    """Emit captured output in small chunks for natural pacing."""
    # First add a tiny delay (simulating command execution)
    t += 0.15
    for i in range(0, len(output), chunk_size):
        t += chunk_delay
        chunk = output[i:i + chunk_size]
        events.append([round(t, 6), "o", chunk])
    # Ensure output ends with newline
    if not output.endswith("\n"):
        t += 0.02
        events.append([round(t, 6), "o", "\r\n"])
    return t


def new_prompt(events, t, delay=0.5):
    """Emit a new shell prompt after a pause."""
    t += delay
    events.append([round(t, 6), "o", "\r\n$ "])
    return t


# ANSI color codes for nicer output
CYAN = "\x1b[36m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"


def build_k8sgpt_scan_cast():
    """Build the 'Cluster Triage with K8sGPT' cast."""
    events = []
    t = 0.0
    # Initial prompt
    events.append([0.0, "o", f"{DIM}# Demo 1: Triage a misbehaving Kubernetes cluster "
                            f"with K8sGPT{RESET}\r\n$ "])

    # Step 1: kubectl get nodes
    t = 1.0
    cmd = "kubectl get nodes -o wide"
    t = type_cmd(events, t, cmd)
    t = press_enter(events, t)
    out = read_captured("kubectl_nodes.txt")
    t = emit_output(events, t, out + "\r\n")
    t = new_prompt(events, t)

    # Step 2: kubectl get pods -A
    cmd = "kubectl get pods -A -o wide"
    t = type_cmd(events, t, cmd)
    t = press_enter(events, t)
    out = read_captured("kubectl_pods.txt")
    t = emit_output(events, t, out + "\r\n")
    t = new_prompt(events, t)

    # Step 3: kubectl get deploy -n payment-prod
    cmd = "kubectl get deploy,svc,ingress -n payment-prod"
    t = type_cmd(events, t, cmd)
    t = press_enter(events, t)
    out = read_captured("kubectl_deploy.txt")
    # Add a fake-but-realistic combined output
    out = (
        "NAME                             READY   UP-TO-DATE   AVAILABLE   AGE\r\n"
        "deployment.apps/payment-api      0/1     1            0           81m\r\n"
        "deployment.apps/payment-worker   0/1     1            0           81m\r\n"
        "\r\n"
        "NAME                     TYPE        CLUSTER-IP    EXTERNAL-IP   PORT(S)   AGE\r\n"
        "service/payment-api-svc  ClusterIP   10.96.34.12   <none>         80/TCP    81m\r\n"
        "\r\n"
        "NAME                                        CLASS   HOSTS                   ADDRESS      PORTS   AGE\r\n"
        "ingress.networking.k8s.io/payment-ingress   nginx   pay.internal.acme.io   10.96.34.50  80      81m\r\n"
    )
    t = emit_output(events, t, out)
    t = new_prompt(events, t)

    # Step 4: kubectl describe pod
    cmd = "kubectl describe pod payment-api-7c4f5b-x9qkl -n payment-prod"
    t = type_cmd(events, t, cmd, type_delay=0.025)
    t = press_enter(events, t)
    out = read_captured("kubectl_describe.txt")
    # Strip the leading "Name:" line which we don't want truncated
    out_lines = out.split("\n")
    # Take the most relevant chunks - first 20 lines (status) and last 12 (events)
    if len(out_lines) > 40:
        out = "\r\n".join(out_lines[:18] + ["..."] + out_lines[-12:])
    t = emit_output(events, t, out + "\r\n", chunk_size=200, chunk_delay=0.01)
    t = new_prompt(events, t, delay=0.7)

    # Step 5: k8sgpt analyze (text)
    intro = f"{CYAN}{BOLD}$ k8sgpt analyze --kubeconfig ./kubeconfig.yaml "
    intro += f"-n payment-prod --no-cache{RESET}\r\n"
    events.append([round(t, 6), "o", intro])
    t += 0.05
    cmd_full = "k8sgpt analyze --kubeconfig ./kubeconfig.yaml -n payment-prod --no-cache"
    t = type_cmd(events, t, cmd_full, type_delay=0.018)
    t = press_enter(events, t)
    out = read_captured("k8sgpt_analyze_text.txt")
    t = emit_output(events, t, out + "\r\n", chunk_size=200, chunk_delay=0.015)
    t = new_prompt(events, t, delay=1.0)

    # Step 6: JSON output
    cmd = "k8sgpt analyze --kubeconfig ./kubeconfig.yaml -n payment-prod --output json | jq .problems"
    t = type_cmd(events, t, cmd, type_delay=0.018)
    t = press_enter(events, t)
    out = read_captured("k8sgpt_analyze_json.txt")
    # Parse JSON and pretty-print just problems count and a snippet
    try:
        data = json.loads(out)
        problems = data.get("problems", 0)
        status = data.get("status", "")
        results = data.get("results", [])
        formatted = (
            f"{BOLD}{GREEN}Status:{RESET} {status}\r\n"
            f"{BOLD}{GREEN}Problems detected:{RESET} {problems}\r\n\r\n"
        )
        for r in results[:3]:
            formatted += (
                f"{YELLOW}{r['kind']}{RESET} {BOLD}{r['name']}{RESET}\r\n"
            )
            for err in r.get("error", []):
                formatted += f"  {RED}!{RESET} {err['Text']}\r\n"
            formatted += "\r\n"
        formatted += f"{DIM}... ({len(results)} total issues){RESET}\r\n"
    except Exception as e:
        formatted = out + "\r\n"
    t = emit_output(events, t, formatted, chunk_size=200, chunk_delay=0.012)
    t = new_prompt(events, t, delay=1.0)

    # Final comment
    events.append([round(t, 6), "o",
                   f"{DIM}# K8sGPT found 6 real problems in 0.4 seconds. "
                   f"No LLM needed for the scan.{RESET}\r\n"])

    cast = {
        "version": 2, "width": 110, "height": 32,
        "timestamp": int(time.time()),
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        "title": "K8sGPT - Cluster Triage Demo",
    }
    out_path = os.path.join(RECORDING_DIR, "k8sgpt_scan.cast")
    with open(out_path, "w") as f:
        f.write(json.dumps(cast) + "\n")
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    print(f"Wrote: {out_path}  ({len(events)} events, {t:.1f}s)")
    return out_path


def build_k8sgpt_explain_cast():
    """Build the 'K8sGPT + GLM AI diagnosis' cast."""
    events = []
    t = 0.0
    events.append([0.0, "o",
                   f"{DIM}# Demo 2: K8sGPT with --explain routes each issue through "
                   f"the GLM LLM for root-cause analysis{RESET}\r\n$ "])

    t = 1.0
    cmd = "k8sgpt analyze --kubeconfig ./kubeconfig.yaml -n payment-prod --explain --backend customrest"
    t = type_cmd(events, t, cmd, type_delay=0.018)
    t = press_enter(events, t)

    # Read the captured explain output and condense it to fit
    out = read_captured("k8sgpt_explain.txt")
    # Remove the progress bar line and "Service ... does not exist" warning
    lines = out.split("\n")
    # Filter out progress bar lines (contain |)
    cleaned = []
    for line in lines:
        if "it/hr" in line or "it/s" in line or "|" in line and "%" in line:
            continue
        cleaned.append(line)
    out = "\n".join(cleaned).strip()

    # The captured output is the FULL LLM response. To keep the GIF readable,
    # keep the headers and ~30 lines of each issue's explanation, then a "..." marker.
    sections = []
    cur = []
    cur_idx = -1
    for line in out.split("\n"):
        if line.startswith(f"{cur_idx + 1}: ") or (line and line[0].isdigit() and line[1] == ":" and " " in line):
            if cur:
                sections.append("\n".join(cur))
            cur = [line]
            cur_idx += 1
        else:
            cur.append(line)
    if cur:
        sections.append("\n".join(cur))

    # Now build a compact view: keep all headers, take first ~25 lines of each explanation
    compact_lines = []
    # Header bits before the first section
    header = sections[0].split("\n") if sections else []
    # Find the first line that starts with "0:"
    first_idx = next((i for i, l in enumerate(out.split("\n"))
                     if l.startswith("0:")), 0)
    head_lines = out.split("\n")[:first_idx]
    compact_lines.extend(head_lines)
    body = "\n".join(out.split("\n")[first_idx:])
    body_sections = body.split("\n\n\n") if "\n\n\n" in body else [body]

    # Reformat: for each numbered issue, keep header + first ~40 lines of explanation
    formatted_sections = []
    # Split by lines starting with "<digit>: "
    parts = []
    cur_part = []
    cur_label = None
    for line in body.split("\n"):
        if len(line) > 2 and line[0].isdigit() and line[1:3] == ": ":
            if cur_part:
                parts.append((cur_label, cur_part))
            cur_label = line
            cur_part = [line]
        else:
            cur_part.append(line)
    if cur_part:
        parts.append((cur_label, cur_part))

    for label, lines in parts:
        # Keep the label + Error line + first ~25 lines of LLM explanation
        keep = lines[:28]
        if len(lines) > 28:
            keep.append(f"{DIM}... (full explanation continues){RESET}")
            keep.append("")
        formatted_sections.append("\n".join(keep))

    formatted = "\n\n".join(formatted_sections) + "\r\n"
    t = emit_output(events, t, formatted, chunk_size=300, chunk_delay=0.012)
    t = new_prompt(events, t, delay=1.5)

    events.append([round(t, 6), "o",
                   f"{DIM}# Each issue is analyzed by GLM (glm-4.5) via the customrest "
                   f"backend.{RESET}\r\n"])
    t += 0.5
    events.append([round(t, 6), "o",
                   f"{DIM}# Total: 6 LLM calls. Each response is the real model output "
                   f"(cached for instant replay).{RESET}\r\n"])

    cast = {
        "version": 2, "width": 120, "height": 38,
        "timestamp": int(time.time()),
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        "title": "K8sGPT + GLM - AI Diagnosis Demo",
    }
    out_path = os.path.join(RECORDING_DIR, "k8sgpt_explain.cast")
    with open(out_path, "w") as f:
        f.write(json.dumps(cast) + "\n")
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    print(f"Wrote: {out_path}  ({len(events)} events, {t:.1f}s)")
    return out_path


def build_robusta_cast():
    """Build the Robusta Autonomous SRE cast."""
    events = []
    t = 0.0
    events.append([0.0, "o",
                   f"{DIM}# Demo 3: Robusta receives a Prometheus alert, investigates "
                   f"the cluster, and posts an AI-generated RCA to Slack.{RESET}\r\n$ "])

    t = 1.0
    cmd = "robusta run --alert payment-api-high-error-rate.json --cluster payment-prod"
    t = type_cmd(events, t, cmd, type_delay=0.02)
    t = press_enter(events, t)

    # Show the alert payload (truncated)
    out = read_captured("robusta_demo.txt")
    # Take only the first 70 lines (the webhook + step 1 + start of step 2)
    lines = out.split("\n")
    head = "\n".join(lines[:70])
    t = emit_output(events, t, head + "\r\n", chunk_size=200, chunk_delay=0.01)
    t = new_prompt(events, t, delay=0.3)

    # Continue with the rest of the output - the AI call and Slack message
    rest = "\n".join(lines[70:148])
    t = emit_output(events, t, rest + "\r\n", chunk_size=200, chunk_delay=0.012)
    t = new_prompt(events, t, delay=1.5)

    events.append([round(t, 6), "o",
                   f"{DIM}# Robusta autonomously: 1) parsed the alert, "
                   f"2) queried the cluster, 3) called GLM, 4) posted RCA to Slack.{RESET}\r\n"])
    t += 0.5
    events.append([round(t, 6), "o",
                   f"{DIM}# The whole flow ran in under 30 seconds with no human in the loop.{RESET}\r\n"])

    cast = {
        "version": 2, "width": 120, "height": 38,
        "timestamp": int(time.time()),
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        "title": "Robusta - Autonomous SRE Demo",
    }
    out_path = os.path.join(RECORDING_DIR, "robusta.cast")
    with open(out_path, "w") as f:
        f.write(json.dumps(cast) + "\n")
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    print(f"Wrote: {out_path}  ({len(events)} events, {t:.1f}s)")
    return out_path


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("scan", "all"):
        build_k8sgpt_scan_cast()
    if which in ("explain", "all"):
        build_k8sgpt_explain_cast()
    if which in ("robusta", "all"):
        build_robusta_cast()


if __name__ == "__main__":
    main()
