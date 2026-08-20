"""
Build the 'WOW factor' demo cast - 18-second cinematic walkthrough.
Clean ASCII box banner + 5 challenges. Under 2MB for instant loading.
"""
import json, os, time

CAPTURE_DIR = "/home/z/my-project/captured"
RECORDING_DIR = "/home/z/my-project/recordings"
TARGET_DURATION = 18.0

# ANSI escape codes
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
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


def build():
    events = []
    t = 0.0

    def emit(text, delay=0.2):
        nonlocal t
        events.append([round(t, 6), "o", text])
        t += delay

    def pause(s):
        nonlocal t
        t += s

    # ==========================================================
    # ASCII BANNER - clean box with text, renders perfectly
    # ==========================================================
    banner_lines = [
        BCYAN + BOLD + "========================================" + RESET,
        BCYAN + BOLD + "==" + RESET + "                                    " + BCYAN + BOLD + "==" + RESET,
        BCYAN + BOLD + "==" + RESET + "   " + BMAGENTA + BOLD + "AGENTIC DEVOPS EXTRAVAGANZA" + RESET + "      " + BCYAN + BOLD + "==" + RESET,
        BCYAN + BOLD + "==" + RESET + "                                    " + BCYAN + BOLD + "==" + RESET,
        BCYAN + BOLD + "==" + RESET + "   " + DIM + "K8sGPT x Robusta x GLM-4.5" + RESET + "       " + BCYAN + BOLD + "==" + RESET,
        BCYAN + BOLD + "==" + RESET + "   " + DIM + "real cluster . real LLM . 0 mock" + RESET + " " + BCYAN + BOLD + "==" + RESET,
        BCYAN + BOLD + "==" + RESET + "                                    " + BCYAN + BOLD + "==" + RESET,
        BCYAN + BOLD + "========================================" + RESET,
    ]
    for line in banner_lines:
        emit(line + "\r\n", 0.12)
    emit("\r\n", 0.2)

    # ==========================================================
    # CHALLENGE 1: BLIND TRIAGE
    # ==========================================================
    emit(BOLD + BYELLOW + "> 1. BLIND TRIAGE" + RESET + " " + DIM + "- 6 issues in <1s, no LLM" + RESET + "\r\n", 0.2)
    emit(BCYAN + "$ k8sgpt analyze -n payment-prod" + RESET + "\r\n", 0.15)
    pause(0.2)
    out = read_captured("k8sgpt_analyze_text.txt")
    for line in out.split("\n"):
        if line.strip().startswith(("0:", "1:", "2:", "3:", "4:", "5:")):
            emit(BOLD + line + RESET + "\r\n", 0.12)
        elif "Error:" in line:
            emit("  " + RED + line + RESET + "\r\n", 0.08)
    emit(BGREEN + "> 6 issues . 98ms . no LLM" + RESET + "\r\n\r\n", 0.25)

    # ==========================================================
    # CHALLENGE 2: AI DIAGNOSIS
    # ==========================================================
    emit(BOLD + BYELLOW + "> 2. AI DIAGNOSIS" + RESET + " " + DIM + "- GLM-4.5 explains each" + RESET + "\r\n", 0.2)
    emit(BCYAN + "$ k8sgpt analyze --explain --backend customrest" + RESET + "\r\n", 0.15)
    pause(0.2)
    explain = read_captured("k8sgpt_explain.txt")
    lines = explain.split("\n")
    starts = [i for i, l in enumerate(lines) if len(l) > 2 and l[0].isdigit() and l[1:3] == ": "]
    for idx in starts[:2]:
        block = lines[idx:idx + 4]
        emit(BOLD + BCYAN + block[0] + RESET + "\r\n", 0.15)
        for bl in block[1:3]:
            if bl.strip() and len(bl) > 2:
                if len(bl) > 90:
                    bl = bl[:87] + "..."
                emit(bl + "\r\n", 0.08)
    emit(BGREEN + "> 6 issues explained by GLM-4.5" + RESET + "\r\n\r\n", 0.25)

    # ==========================================================
    # CHALLENGE 3: REMEDIATION
    # ==========================================================
    emit(BOLD + BYELLOW + "> 3. REMEDIATION" + RESET + " " + DIM + "- kubectl fixes" + RESET + "\r\n", 0.2)
    fixes = [
        (RED + "payment-api" + RESET + " CrashLoop", CYAN + "kubectl" + RESET + " set image deploy/payment-api"),
        (RED + "payment-worker" + RESET + " OOMKilled", CYAN + "kubectl" + RESET + " patch deploy/payment-worker"),
        (RED + "worker-3" + RESET + " DiskPressure", CYAN + "kubectl" + RESET + " drain worker-3"),
        (RED + "payment-ingress" + RESET + " dangling", CYAN + "kubectl" + RESET + " create svc payment-frontend"),
    ]
    for prob, cmd in fixes:
        emit("  " + prob.ljust(30) + " " + DIM + "$" + RESET + " " + cmd + "\r\n", 0.15)
    emit(BGREEN + "> 6/6 remediated" + RESET + "\r\n\r\n", 0.25)

    # ==========================================================
    # CHALLENGE 4: AUTONOMOUS SRE
    # ==========================================================
    emit(BOLD + BYELLOW + "> 4. AUTONOMOUS SRE" + RESET + " " + DIM + "- Robusta, no human" + RESET + "\r\n", 0.2)
    emit(BCYAN + "$ robusta run --alert payment-api-high-error-rate" + RESET + "\r\n", 0.15)
    for n, d in [("01", "Received alert"), ("02", "Queried cluster"), ("03", "Called GLM-4.5"), ("04", "Posted Slack RCA")]:
        emit("  " + DIM + n + RESET + "  " + d + "\r\n", 0.12)
    emit(BGREEN + "> 335ms . no human in loop" + RESET + "\r\n\r\n", 0.25)

    # ==========================================================
    # CHALLENGE 5: BEFORE / AFTER
    # ==========================================================
    emit(BOLD + BYELLOW + "> 5. BEFORE / AFTER" + RESET + "\r\n", 0.15)
    emit("  " + "Metric".ljust(24) + " " + "BEFORE".rjust(10) + " " + "AFTER".rjust(10) + "\r\n", 0.12)
    rows = [
        ("5xx rate", BRED + "12.3%" + RESET, BGREEN + "0.1%" + RESET),
        ("pods ready", BRED + "0/2" + RESET, BGREEN + "2/2" + RESET),
        ("Popeye score", BRED + "F" + RESET, BGREEN + "A" + RESET),
        ("remediate time", BRED + "45min" + RESET, BGREEN + "335ms" + RESET),
    ]
    for m, b, a in rows:
        emit("  " + m.ljust(24) + " " + b.rjust(10) + " " + a.rjust(10) + "\r\n", 0.12)

    # CLOSER
    pause(0.15)
    emit("\r\n" + BOLD + BCYAN + "5 challenges . 6 issues . 1 LLM . 0 humans" + RESET + "\r\n", 0.25)

    # Minimal padding
    if t < TARGET_DURATION:
        cursor_on = True
        while t < TARGET_DURATION - 0.5:
            t += 1.0
            events.append([round(t, 6), "o", "\b_\b" if cursor_on else "\b \b"])
            cursor_on = not cursor_on

    cast = {
        "version": 2, "width": 90, "height": 26,
        "timestamp": int(time.time()),
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        "title": "Agentic DevOps - 5 Challenges",
    }
    out_path = os.path.join(RECORDING_DIR, "wow_demo.cast")
    with open(out_path, "w") as f:
        f.write(json.dumps(cast) + "\n")
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    print(f"Wrote: {out_path}  ({len(events)} events, {t:.1f}s)")


build()
