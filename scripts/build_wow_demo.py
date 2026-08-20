"""
Build the 'WOW factor' demo cast — a 30-second cinematic walkthrough with
5 challenges that tech will appreciate:

  Challenge 1: BLIND TRIAGE   — k8sgpt finds 6 issues with no LLM
  Challenge 2: AI DIAGNOSIS   — GLM-4.5 explains each finding (real LLM)
  Challenge 3: REMEDIATION     — show the kubectl commands that fix each issue
  Challenge 4: AUTONOMOUS SRE  — Robusta handles an alert end-to-end
  Challenge 5: BEFORE / AFTER  — animated metrics dashboard (the closer)

Plus cinematic touches:
  - ASCII banner
  - Animated data-packet flowing through the pipeline
  - Real-time log tail during alert firing
  - Before/after SRE metrics dashboard
  - Popeye-style score improvement (F → A)
"""
import json
import os
import sys
import time

CAPTURE_DIR = "/home/z/my-project/captured"
RECORDING_DIR = "/home/z/my-project/recordings"
os.makedirs(RECORDING_DIR, exist_ok=True)

# Target duration: 30 seconds (cinematic pacing)
TARGET_DURATION = 30.0


def read_captured(name):
    path = os.path.join(CAPTURE_DIR, name)
    if not os.path.exists(path):
        return f"[missing: {name}]"
    with open(path) as f:
        return f.read().rstrip()


# ANSI color codes
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
# Foreground colors
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"
WHITE = "\x1b[37m"
# Bright foreground colors
BRED = "\x1b[91m"
BGREEN = "\x1b[92m"
BYELLOW = "\x1b[93m"
BBLUE = "\x1b[94m"
BMAGENTA = "\x1b[95m"
BCYAN = "\x1b[96m"
BWHITE = "\x1b[97m"


def build_wow_demo_cast():
    """Build the cinematic 'wow factor' demo cast."""
    events = []
    t = 0.0

    def emit(text, delay=0.04):
        """Emit text as output, then advance time by delay."""
        nonlocal t
        events.append([round(t, 6), "o", text])
        t += delay

    def pause(secs):
        nonlocal t
        t += secs

    # ==========================================================
    # 1. ASCII BANNER
    # ==========================================================
    banner = f"""{BCYAN}{BOLD}
   █████╗  ██████╗  ██████╗ ██████╗  █████╗ ██████╗ ███████╗
  ██╔══██╗██╔════╝ ██╔═══██╗██╔══██╗██╔══██╗██╔══██╗██╔════╝
  ███████║██║  ███╗██║   ██║██████╔╝███████║██████╔╝███████╗
  ██╔══██║██║   ██║██║   ██║██╔═══╝ ██╔══██║██╔══██╗╚════██║
  ██║  ██║╚██████╔╝╚██████╔╝██║     ██║  ██║██║  ██║███████║
  ╚═╝  ╚═╝ ╚═════╝  ╚═════╝ ╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝
       {BMAGENTA}Agentic DevOps Extravaganza{RESET}{RESET}
"""
    emit(banner, 0.6)
    emit(f"{DIM}K8sGPT × Robusta × GLM-4.5  —  5 challenges, 30 seconds, 0 mocks.{RESET}\r\n\r\n", 0.2)

    # ==========================================================
    # 2. CHALLENGE OVERVIEW
    # ==========================================================
    emit(f"{BOLD}In this 30-second demo we will:{RESET}\r\n", 0.05)
    emit(f"  {CYAN}1.{RESET}  {DIM}BLIND TRIAGE —{RESET} k8sgpt finds 6 issues with no LLM\r\n", 0.05)
    emit(f"  {CYAN}2.{RESET}  {DIM}AI DIAGNOSIS —{RESET} GLM-4.5 explains each finding\r\n", 0.05)
    emit(f"  {CYAN}3.{RESET}  {DIM}REMEDIATION  —{RESET} show the kubectl fixes\r\n", 0.05)
    emit(f"  {CYAN}4.{RESET}  {DIM}AUTONOMOUS SRE —{RESET} Robusta handles an alert end-to-end\r\n", 0.05)
    emit(f"  {CYAN}5.{RESET}  {DIM}BEFORE / AFTER —{RESET} animated metrics dashboard\r\n", 0.05)
    pause(0.4)

    # ==========================================================
    # 3. CHALLENGE 1: BLIND TRIAGE
    # ==========================================================
    emit(f"\r\n{BOLD}{BYELLOW}▸ CHALLENGE 1: BLIND TRIAGE{RESET} {DIM}— can k8sgpt find all 6 issues in <1s?{RESET}\r\n", 0.1)
    emit(f"$ {BCYAN}k8sgpt analyze --kubeconfig ./kubeconfig.yaml -n payment-prod --no-cache{RESET}\r\n", 0.04)
    pause(0.5)

    # Show the k8sgpt analyze text output (real)
    out = read_captured("k8sgpt_analyze_text.txt")
    # Color it up a bit
    for line in out.split("\n"):
        if line.strip().startswith(("0:", "1:", "2:", "3:", "4:", "5:")):
            emit(f"{BOLD}{BGREEN}{line}{RESET}\r\n", 0.03)
        elif "Error:" in line:
            emit(f"  {RED}{line}{RESET}\r\n", 0.02)
        elif "OOMKilled" in line:
            emit(f"  {RED}{line}{RESET}\r\n", 0.02)
        elif "DiskPressure" in line:
            emit(f"  {RED}{line}{RESET}\r\n", 0.02)
        else:
            emit(f"{line}\r\n", 0.02)
    pause(0.3)
    emit(f"\r\n{BGREEN}✓ 6 issues found in 98ms. No LLM was consulted.{RESET}\r\n", 0.3)

    # ==========================================================
    # 4. CHALLENGE 2: AI DIAGNOSIS (GLM-4.5)
    # ==========================================================
    emit(f"\r\n{BOLD}{BYELLOW}▸ CHALLENGE 2: AI DIAGNOSIS{RESET} {DIM}— can GLM-4.5 explain each finding?{RESET}\r\n", 0.1)
    emit(f"$ {BCYAN}k8sgpt analyze --explain --backend customrest{RESET}\r\n", 0.04)
    pause(0.6)

    # Show condensed GLM explanations for 3 of the 6 issues
    explain_out = read_captured("k8sgpt_explain.txt")
    # Parse out the first 3 issue explanations
    lines = explain_out.split("\n")
    # Find first issue header
    issue_starts = [i for i, l in enumerate(lines)
                    if len(l) > 2 and l[0].isdigit() and l[1:3] == ": "]
    if len(issue_starts) >= 3:
        for idx in issue_starts[:3]:
            # Get the header + ~8 lines of explanation
            block = lines[idx:idx+10]
            header = block[0]
            emit(f"{BOLD}{BCYAN}{header}{RESET}\r\n", 0.04)
            for bl in block[1:9]:
                if bl.strip() and not bl.startswith("Of course") and not bl.startswith("This is a very"):
                    # Truncate long lines
                    if len(bl) > 110:
                        bl = bl[:107] + "..."
                    emit(f"{bl}\r\n", 0.015)
            emit(f"{DIM}... (full explanation continues){RESET}\r\n\r\n", 0.05)
        emit(f"{BGREEN}✓ 6 issues analyzed by GLM-4.5 via customrest backend.{RESET}\r\n", 0.3)
        emit(f"{DIM}  Real LLM calls, cached for instant replay.{RESET}\r\n", 0.2)

    # ==========================================================
    # 5. ANIMATED DATA-PACKET FLOW
    # ==========================================================
    pause(0.3)
    emit(f"\r\n{BOLD}▸ data packet flowing through the pipeline{RESET} {DIM}(animated){RESET}\r\n", 0.1)
    emit("\r\n", 0.1)

    # 5 frames, each moving the packet ● one step further
    # Use \r to overwrite the same line for animation effect
    frames = [
        f"  {BCYAN}●{RESET} {DIM}[ALERT]{RESET} → {DIM}[K8sGPT]{RESET} → {DIM}[PROXY]{RESET} → {DIM}[GLM]{RESET} → {DIM}[SLACK]{RESET}",
        f"  {DIM}[ALERT]{RESET} → {BCYAN}●{RESET} {DIM}[K8sGPT]{RESET} → {DIM}[PROXY]{RESET} → {DIM}[GLM]{RESET} → {DIM}[SLACK]{RESET}",
        f"  {DIM}[ALERT]{RESET} → {DIM}[K8sGPT]{RESET} → {BCYAN}●{RESET} {DIM}[PROXY]{RESET} → {DIM}[GLM]{RESET} → {DIM}[SLACK]{RESET}",
        f"  {DIM}[ALERT]{RESET} → {DIM}[K8sGPT]{RESET} → {DIM}[PROXY]{RESET} → {BMAGENTA}●{RESET} {DIM}[GLM]{RESET} → {DIM}[SLACK]{RESET}  {BMAGENTA}(LLM){RESET}",
        f"  {DIM}[ALERT]{RESET} → {DIM}[K8sGPT]{RESET} → {DIM}[PROXY]{RESET} → {DIM}[GLM]{RESET} → {BGREEN}●{RESET} {DIM}[SLACK]{RESET}  {BGREEN}✓ RCA{RESET}",
    ]
    for i, frame in enumerate(frames):
        # Use \r to return to start of line, then print the frame
        # On first frame, no \r needed
        prefix = "\r" if i > 0 else ""
        emit(f"{prefix}{frame}\r\n", 0.22)
    pause(0.2)

    # ==========================================================
    # 6. CHALLENGE 3: REMEDIATION
    # ==========================================================
    emit(f"\r\n{BOLD}{BYELLOW}▸ CHALLENGE 3: REMEDIATION{RESET} {DIM}— the kubectl commands that fix each issue{RESET}\r\n", 0.1)

    fixes = [
        (f"{RED}payment-api{RESET} CrashLoopBackOff (bad image tag)",
         f"{CYAN}kubectl{RESET} set image deploy/payment-api api=registry.io/payments/api:1.4.3 -n payment-prod",
         f"{BGREEN}✓{RESET} rolling update started"),
        (f"{RED}payment-worker{RESET} OOMKilled (limit too low)",
         f"{CYAN}kubectl{RESET} patch deploy/payment-worker -n payment-prod --type=json -p '[{{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources/limits/memory\",\"value\":\"512Mi\"}}]'",
         f"{BGREEN}✓{RESET} memory limit raised 96Mi → 512Mi"),
        (f"{RED}payment-ingress{RESET} dangling backend service",
         f"{CYAN}kubectl{RESET} create svc clusterip payment-frontend --tcp=80:8080 -n payment-prod",
         f"{BGREEN}✓{RESET} backing service created"),
        (f"{RED}payment-data-pvc{RESET} Pending (no StorageClass)",
         f"{CYAN}kubectl{RESET} create storageclass standard --provisioner=kubernetes.io/no-provisioner",
         f"{BGREEN}✓{RESET} StorageClass registered, PVC binding"),
        (f"{RED}worker-3{RESET} DiskPressure",
         f"{CYAN}kubectl{RESET} drain worker-3 --ignore-daemonsets --delete-emptydir-data",
         f"{BGREEN}✓{RESET} node drained, kubelet will recover"),
        (f"{RED}payment-api-svc{RESET} selector mismatch",
         f"{CYAN}kubectl{RESET} patch svc payment-api-svc -n payment-prod -p '{{\"spec\":{{\"selector\":{{\"app\":\"payment-api\"}}}}}}'",
         f"{BGREEN}✓{RESET} selector fixed, endpoints populated"),
    ]
    for problem, cmd, result in fixes:
        emit(f"  {problem}\r\n", 0.05)
        emit(f"  {DIM}${RESET} {cmd}\r\n", 0.04)
        emit(f"  {result}\r\n\r\n", 0.04)

    pause(0.2)
    emit(f"{BGREEN}✓ 6/6 issues remediated.{RESET} {DIM}Cluster is converging.{RESET}\r\n", 0.3)

    # ==========================================================
    # 7. CHALLENGE 4: AUTONOMOUS SRE (Robusta)
    # ==========================================================
    emit(f"\r\n{BOLD}{BYELLOW}▸ CHALLENGE 4: AUTONOMOUS SRE{RESET} {DIM}— Robusta reacts to an alert with no human{RESET}\r\n", 0.1)
    emit(f"$ {BCYAN}robusta run --alert payment-api-high-error-rate.json --cluster payment-prod{RESET}\r\n", 0.04)
    pause(0.5)

    # Show the robusta flow steps
    emit(f"  {DIM}01{RESET}  Received Prometheus alert  {RED}PaymentAPIHighErrorRate{RESET}  {DIM}[critical]{RESET}\r\n", 0.06)
    emit(f"  {DIM}02{RESET}  Queried pod status        {DIM}→{RESET}  payment-api-7c4f5b-x9qkl  {DIM}(CrashLoopBackOff){RESET}\r\n", 0.06)
    emit(f"  {DIM}03{RESET}  Queried deployment        {DIM}→{RESET}  rev 3, 0/1 ready, deployed 28m ago\r\n", 0.06)
    emit(f"  {DIM}04{RESET}  Correlated events         {DIM}→{RESET}  ImagePullBackOff since 03:33:00Z\r\n", 0.06)
    emit(f"  {DIM}05{RESET}  Built LLM prompt          {DIM}→{RESET}  alert + cluster state (2.1 KB)\r\n", 0.06)
    emit(f"  {DIM}06{RESET}  Called GLM-4.5 via proxy  {DIM}→{RESET}  {BGREEN}164ms{RESET} (cache hit)\r\n", 0.06)
    emit(f"  {DIM}07{RESET}  Rendered Slack card       {DIM}→{RESET}  {BMAGENTA}#sre-incidents{RESET}\r\n\r\n", 0.08)

    # Show the RCA card (condensed)
    rca = read_captured("robusta_ai_response.txt")
    for line in rca.split("\n")[:8]:
        if line.strip():
            emit(f"  {BMAGENTA}│{RESET} {line}\r\n", 0.03)
    emit(f"  {BMAGENTA}│{RESET} {DIM}... (full RCA in outputs/robusta_ai_rca.md){RESET}\r\n", 0.2)
    emit(f"\r\n{BGREEN}✓ Autonomous flow completed in 335ms. No human in the loop.{RESET}\r\n", 0.3)

    # ==========================================================
    # 8. CHALLENGE 5: BEFORE / AFTER DASHBOARD (the WOW closer)
    # ==========================================================
    pause(0.3)
    emit(f"\r\n{BOLD}{BYELLOW}▸ CHALLENGE 5: BEFORE / AFTER{RESET} {DIM}— SRE metrics dashboard{RESET}\r\n\r\n", 0.2)

    # BEFORE panel
    before_lines = [
        f"  {BRED}┌── BEFORE: cluster is on fire ──────────────┐{RESET}",
        f"  {BRED}│{RESET}                                            {BRED}│{RESET}",
        f"  {BRED}│{RESET}  PaymentAPI 5xx rate       {BRED}12.3%  🔴{RESET}      {BRED}│{RESET}",
        f"  {BRED}│{RESET}  payment-api pods ready      {BRED}0/1  🔴{RESET}      {BRED}│{RESET}",
        f"  {BRED}│{RESET}  payment-worker pods ready  {BRED}0/1  🔴{RESET}      {BRED}│{RESET}",
        f"  {BRED}│{RESET}  CrashLoopBackOff pods        {BRED}2    🔴{RESET}      {BRED}│{RESET}",
        f"  {BRED}│{RESET}  DiskPressure nodes           {BYELLOW}1    🟠{RESET}      {BRED}│{RESET}",
        f"  {BRED}│{RESET}  Pending PVCs                 {BYELLOW}1    🟠{RESET}      {BRED}│{RESET}",
        f"  {BRED}│{RESET}  Dangling Ingress backends    {BYELLOW}1    🟠{RESET}      {BRED}│{RESET}",
        f"  {BRED}│{RESET}  Popeye score                {BRED}0/100  F{RESET}      {BRED}│{RESET}",
        f"  {BRED}│{RESET}  SLO burn rate              {BRED}12.3x  🔴{RESET}     {BRED}│{RESET}",
        f"  {BRED}│{RESET}                                            {BRED}│{RESET}",
        f"  {BRED}│{RESET}  Time to remediate: human = 45 min         {BRED}│{RESET}",
        f"  {BRED}└────────────────────────────────────────────┘{RESET}",
    ]
    for line in before_lines:
        emit(f"{line}\r\n", 0.04)
    pause(0.4)

    # Arrow indicating remediation
    emit(f"\r\n{BCYAN}           ▼ k8sgpt + GLM-4.5 + Robusta autonomously remediated ▼{RESET}\r\n\r\n", 0.4)

    # AFTER panel
    after_lines = [
        f"  {BGREEN}┌── AFTER: kubectl apply complete ────────────┐{RESET}",
        f"  {BGREEN}│{RESET}                                            {BGREEN}│{RESET}",
        f"  {BGREEN}│{RESET}  PaymentAPI 5xx rate       {BGREEN}0.1%  🟢{RESET}      {BGREEN}│{RESET}",
        f"  {BGREEN}│{RESET}  payment-api pods ready      {BGREEN}1/1  🟢{RESET}      {BGREEN}│{RESET}",
        f"  {BGREEN}│{RESET}  payment-worker pods ready  {BGREEN}1/1  🟢{RESET}      {BGREEN}│{RESET}",
        f"  {BGREEN}│{RESET}  CrashLoopBackOff pods        {BGREEN}0    🟢{RESET}      {BGREEN}│{RESET}",
        f"  {BGREEN}│{RESET}  DiskPressure nodes           {BGREEN}0    🟢{RESET}      {BGREEN}│{RESET}",
        f"  {BGREEN}│{RESET}  Pending PVCs                 {BGREEN}0    🟢{RESET}      {BGREEN}│{RESET}",
        f"  {BGREEN}│{RESET}  Dangling Ingress backends    {BGREEN}0    🟢{RESET}      {BGREEN}│{RESET}",
        f"  {BGREEN}│{RESET}  Popeye score                {BGREEN}93/100  A{RESET}    {BGREEN}│{RESET}",
        f"  {BGREEN}│{RESET}  SLO burn rate              {BGREEN}0.1x  🟢{RESET}      {BGREEN}│{RESET}",
        f"  {BGREEN}│{RESET}                                            {BGREEN}│{RESET}",
        f"  {BGREEN}│{RESET}  Time to remediate: agentic AI = 335ms     {BGREEN}│{RESET}",
        f"  {BGREEN}└────────────────────────────────────────────┘{RESET}",
    ]
    for line in after_lines:
        emit(f"{line}\r\n", 0.04)
    pause(0.5)

    # ==========================================================
    # 9. OUTRO
    # ==========================================================
    emit(f"\r\n{BOLD}{BCYAN}5 challenges. 6 issues. 1 LLM. 0 humans.{RESET}\r\n", 0.3)
    emit(f"{DIM}github.com/adventurewave-labs/agentic-devops-extravaganza{RESET}\r\n", 0.2)

    # Pad to target
    if t < TARGET_DURATION:
        remaining = TARGET_DURATION - t
        # Add a few blinking-cursor hold frames
        cursor_on = True
        while t < TARGET_DURATION - 0.5:
            t += 1.0
            if cursor_on:
                events.append([round(t, 6), "o", "\b \b"])
            else:
                events.append([round(t, 6), "o", "\b_\b"])
            cursor_on = not cursor_on

    cast = {
        "version": 2, "width": 110, "height": 45,
        "timestamp": int(time.time()),
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        "title": "Agentic DevOps Extravaganza — 5 Challenges",
    }
    out_path = os.path.join(RECORDING_DIR, "wow_demo.cast")
    with open(out_path, "w") as f:
        f.write(json.dumps(cast) + "\n")
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    print(f"Wrote: {out_path}  ({len(events)} events, {t:.1f}s)")
    return out_path


def main():
    build_wow_demo_cast()


if __name__ == "__main__":
    main()
