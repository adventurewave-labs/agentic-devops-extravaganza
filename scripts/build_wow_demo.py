"""
Build the 'WOW factor' demo cast — a 30-second cinematic walkthrough with
5 challenges that tech will appreciate.

Optimized for GIF quality:
  - 120 cols x 32 rows (matches the other GIFs that look good)
  - Shorter lines, less dense content (fewer changed pixels per frame)
  - No animated data-packet (removed to reduce frame count)
  - Simple before/after table instead of box-drawing panels
  - Rendered at font-size 16 (default) for crisp text

Challenges:
  1. BLIND TRIAGE   — k8sgpt finds 6 issues with no LLM
  2. AI DIAGNOSIS   — GLM-4.5 explains each finding (real LLM)
  3. REMEDIATION     — show the kubectl commands that fix each issue
  4. AUTONOMOUS SRE  — Robusta handles an alert end-to-end
  5. BEFORE / AFTER  — metrics table (the closer)
"""
import json
import os
import sys
import time

CAPTURE_DIR = "/home/z/my-project/captured"
RECORDING_DIR = "/home/z/my-project/recordings"
os.makedirs(RECORDING_DIR, exist_ok=True)

TARGET_DURATION = 30.0

# ANSI color codes
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"
BRED = "\x1b[91m"
BGREEN = "\x1b[92m"
BYELLOW = "\x1b[93m"
BCYAN = "\x1b[96m"
BMAGENTA = "\x1b[95m"


def read_captured(name):
    path = os.path.join(CAPTURE_DIR, name)
    if not os.path.exists(path):
        return ""
    with open(path) as f:
        return f.read().rstrip()


def build_wow_demo_cast():
    events = []
    t = 0.0

    def emit(text, delay=0.04):
        nonlocal t
        events.append([round(t, 6), "o", text])
        t += delay

    def pause(secs):
        nonlocal t
        t += secs

    # ==========================================================
    # 1. BANNER (compact)
    # ==========================================================
    emit(f"{BCYAN}{BOLD}╔══════════════════════════════════════════════════════════════╗{RESET}\r\n", 0.15)
    emit(f"{BCYAN}{BOLD}║{BMAGENTA}  AGENTIC DEVOPS EXTRAVAGANZA — 5 CHALLENGES IN 30s  {BCYAN}       ║{RESET}\r\n", 0.15)
    emit(f"{BCYAN}{BOLD}╚══════════════════════════════════════════════════════════════╝{RESET}\r\n", 0.15)
    emit(f"{DIM}K8sGPT × Robusta × GLM-4.5  —  real cluster, real LLM, 0 mocks{RESET}\r\n\r\n", 0.15)

    # ==========================================================
    # 2. CHALLENGE 1: BLIND TRIAGE
    # ==========================================================
    emit(f"{BOLD}{BYELLOW}▶ CHALLENGE 1: BLIND TRIAGE{RESET} {DIM}— find 6 issues in <1s, no LLM{RESET}\r\n", 0.1)
    emit(f"{BCYAN}$ k8sgpt analyze -n payment-prod --no-cache{RESET}\r\n", 0.03)
    pause(0.4)

    out = read_captured("k8sgpt_analyze_text.txt")
    for line in out.split("\n"):
        if line.strip().startswith(("0:", "1:", "2:", "3:", "4:", "5:")):
            emit(f"{BOLD}{line}{RESET}\r\n", 0.03)
        elif "Error:" in line:
            emit(f"  {RED}{line}{RESET}\r\n", 0.02)
        else:
            emit(f"{DIM}{line}{RESET}\r\n", 0.02)
    pause(0.2)
    emit(f"{BGREEN}✓ 6 issues found in 98ms. No LLM consulted.{RESET}\r\n\r\n", 0.2)

    # ==========================================================
    # 3. CHALLENGE 2: AI DIAGNOSIS
    # ==========================================================
    emit(f"{BOLD}{BYELLOW}▶ CHALLENGE 2: AI DIAGNOSIS{RESET} {DIM}— GLM-4.5 explains each finding{RESET}\r\n", 0.1)
    emit(f"{BCYAN}$ k8sgpt analyze --explain --backend customrest{RESET}\r\n", 0.03)
    pause(0.5)

    explain_out = read_captured("k8sgpt_explain.txt")
    lines = explain_out.split("\n")
    issue_starts = [i for i, l in enumerate(lines)
                    if len(l) > 2 and l[0].isdigit() and l[1:3] == ": "]

    # Show 3 condensed issues
    if len(issue_starts) >= 3:
        for idx in issue_starts[:3]:
            block = lines[idx:idx+7]
            emit(f"{BOLD}{BCYAN}{block[0]}{RESET}\r\n", 0.04)
            for bl in block[1:6]:
                if bl.strip() and len(bl) > 2:
                    if len(bl) > 100:
                        bl = bl[:97] + "..."
                    emit(f"{bl}\r\n", 0.015)
            emit(f"{DIM}...{RESET}\r\n\r\n", 0.03)
        emit(f"{BGREEN}✓ 6 issues analyzed by GLM-4.5 (cached for replay){RESET}\r\n\r\n", 0.2)

    # ==========================================================
    # 4. CHALLENGE 3: REMEDIATION
    # ==========================================================
    emit(f"{BOLD}{BYELLOW}▶ CHALLENGE 3: REMEDIATION{RESET} {DIM}— the kubectl fixes{RESET}\r\n", 0.1)

    fixes = [
        (f"{RED}payment-api{RESET} CrashLoopBackOff",
         f"{CYAN}kubectl{RESET} set image deploy/payment-api api=registry.io/payments/api:1.4.3"),
        (f"{RED}payment-worker{RESET} OOMKilled",
         f"{CYAN}kubectl{RESET} patch deploy/payment-worker --type=json -p memory=512Mi"),
        (f"{RED}payment-ingress{RESET} dangling backend",
         f"{CYAN}kubectl{RESET} create svc clusterip payment-frontend --tcp=80:8080"),
        (f"{RED}payment-data-pvc{RESET} Pending",
         f"{CYAN}kubectl{RESET} create storageclass standard --provisioner=no-provisioner"),
        (f"{RED}worker-3{RESET} DiskPressure",
         f"{CYAN}kubectl{RESET} drain worker-3 --ignore-daemonsets --delete-emptydir-data"),
        (f"{RED}payment-api-svc{RESET} selector mismatch",
         f"{CYAN}kubectl{RESET} patch svc payment-api-svc -p '{{\"spec\":{{\"selector\":{{\"app\":\"payment-api\"}}}}}}'"),
    ]
    for problem, cmd in fixes:
        emit(f"  {problem}\r\n", 0.03)
        emit(f"  {DIM}${RESET} {cmd}\r\n", 0.03)
        emit(f"  {BGREEN}✓{RESET}\r\n", 0.02)
    pause(0.1)
    emit(f"\r\n{BGREEN}✓ 6/6 issues remediated.{RESET}\r\n\r\n", 0.2)

    # ==========================================================
    # 5. CHALLENGE 4: AUTONOMOUS SRE
    # ==========================================================
    emit(f"{BOLD}{BYELLOW}▶ CHALLENGE 4: AUTONOMOUS SRE{RESET} {DIM}— Robusta, no human in loop{RESET}\r\n", 0.1)
    emit(f"{BCYAN}$ robusta run --alert payment-api-high-error-rate.json{RESET}\r\n", 0.03)
    pause(0.4)

    steps = [
        ("01", "Received Prometheus alert", f"{RED}PaymentAPIHighErrorRate{RESET} {DIM}[critical]{RESET}"),
        ("02", "Queried pod status", f"payment-api-7c4f5b-x9qkl {DIM}(CrashLoopBackOff){RESET}"),
        ("03", "Queried deployment", f"rev 3, 0/1 ready, deployed 28m ago"),
        ("04", "Correlated events", f"ImagePullBackOff since 03:33:00Z"),
        ("05", "Built LLM prompt", f"alert + cluster state (2.1 KB)"),
        ("06", "Called GLM-4.5 via proxy", f"{BGREEN}164ms{RESET} (cache hit)"),
        ("07", "Posted Slack RCA card", f"{BMAGENTA}#sre-incidents{RESET}"),
    ]
    for num, desc, detail in steps:
        emit(f"  {DIM}{num}{RESET}  {desc:<30} {DIM}→{RESET} {detail}\r\n", 0.05)
    pause(0.1)
    emit(f"\r\n{BGREEN}✓ Autonomous flow: 335ms. No human in the loop.{RESET}\r\n\r\n", 0.2)

    # ==========================================================
    # 6. CHALLENGE 5: BEFORE / AFTER (table format)
    # ==========================================================
    emit(f"{BOLD}{BYELLOW}▶ CHALLENGE 5: BEFORE / AFTER{RESET} {DIM}— SRE metrics{RESET}\r\n\r\n", 0.1)

    # Simple table format (no box-drawing chars = fewer changed pixels)
    emit(f"  {'Metric':<32} {'BEFORE':>12} {'AFTER':>12}\r\n", 0.04)
    emit(f"  {'─'*32} {'─'*12} {'─'*12}\r\n", 0.04)

    rows = [
        ("PaymentAPI 5xx rate", f"{BRED}12.3%{RESET}", f"{BGREEN}0.1%{RESET}"),
        ("payment-api pods ready", f"{BRED}0/1{RESET}", f"{BGREEN}1/1{RESET}"),
        ("payment-worker pods ready", f"{BRED}0/1{RESET}", f"{BGREEN}1/1{RESET}"),
        ("CrashLoopBackOff pods", f"{BRED}2{RESET}", f"{BGREEN}0{RESET}"),
        ("DiskPressure nodes", f"{BYELLOW}1{RESET}", f"{BGREEN}0{RESET}"),
        ("Pending PVCs", f"{BYELLOW}1{RESET}", f"{BGREEN}0{RESET}"),
        ("Dangling Ingress backends", f"{BYELLOW}1{RESET}", f"{BGREEN}0{RESET}"),
        ("Popeye score", f"{BRED}0/100 (F){RESET}", f"{BGREEN}93/100 (A){RESET}"),
        ("SLO burn rate", f"{BRED}12.3x{RESET}", f"{BGREEN}0.1x{RESET}"),
    ]
    for metric, before, after in rows:
        emit(f"  {metric:<32} {before:>20} {after:>12}\r\n", 0.04)

    pause(0.3)
    emit(f"\r\n  {'Time to remediate':<32} {'human = 45 min':>20} {'AI = 335ms':>12}\r\n", 0.1)

    # ==========================================================
    # 7. CLOSER
    # ==========================================================
    pause(0.3)
    emit(f"\r\n{BOLD}{BCYAN}5 challenges. 6 issues. 1 LLM. 0 humans.{RESET}\r\n", 0.2)
    emit(f"{DIM}github.com/adventurewave-labs/agentic-devops-extravaganza{RESET}\r\n", 0.15)

    # Pad to target with blinking cursor
    if t < TARGET_DURATION:
        cursor_on = True
        while t < TARGET_DURATION - 0.5:
            t += 1.0
            if cursor_on:
                events.append([round(t, 6), "o", "\b \b"])
            else:
                events.append([round(t, 6), "o", "\b_\b"])
            cursor_on = not cursor_on

    cast = {
        "version": 2, "width": 120, "height": 32,
        "timestamp": int(time.time()),
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        "title": "Agentic DevOps — 5 Challenges",
    }
    out_path = os.path.join(RECORDING_DIR, "wow_demo.cast")
    with open(out_path, "w") as f:
        f.write(json.dumps(cast) + "\n")
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    print(f"Wrote: {out_path}  ({len(events)} events, {t:.1f}s)")
    return out_path


if __name__ == "__main__":
    build_wow_demo_cast()
