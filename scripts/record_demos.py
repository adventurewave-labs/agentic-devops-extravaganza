"""
Record the demo GIFs from LIVE terminal sessions.

WHY THIS FILE REPLACED build_casts.py / build_wow_demo.py
    Those scripts hand-authored asciinema cast files event by event: they
    emitted fake keystroke timings, replayed text captured earlier, and — in
    the "wow" demo — printed invented numbers (a 6/6 remediation that ran no
    commands, a Popeye score for a tool the repo never invoked, a 45min→335ms
    table). The README described the result as "verbatim from the real
    binaries — no mocks, no edits", which was not true of that GIF.

    This script runs the real commands in a real pty and records what actually
    comes back, with real timings. Every character in the resulting cast was
    produced by a process on your machine. If a command fails, the failure is
    in the GIF.

    The one piece of formatting is the prompt line: `runcmd` prints the
    command and then executes that same argument list, so the displayed
    command and the executed command cannot drift apart.

Usage:
    python scripts/record_demos.py [scan|explain|remediate|triage|all]
                                   [--no-gif]

GIFs are rendered by scripts/cast_to_gif.py (pure Python + Pillow), so this
works on a clean clone with no extra binaries. If `agg` happens to be on
PATH it is used instead. The .cast files are plain asciinema v2 and play with
`asciinema play recordings/<name>.cast`.
"""
import argparse
import json
import os
import pty
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

COLS, ROWS = 100, 30

PRELUDE = r'''
runcmd() {
  printf '\033[1;36m$ %s\033[0m\n' "$*"
  "$@"
  printf '\n'
}
note() { printf '\033[2m# %s\033[0m\n' "$*"; }
findings() { k8sgpt analyze --kubecontext mock-context --no-cache -n payment-prod 2>/dev/null | grep -cE '^[0-9]+: '; }
health()   { curl -sk https://127.0.0.1:8443/_demo/health | python3 -m json.tool; }
'''

DEMOS = {
    "scan": {
        "title": "k8sgpt finds the broken resources — no LLM",
        "script": r'''
note "a deliberately broken payment-prod cluster, served over the real Kubernetes API"
runcmd kubectl --insecure-skip-tls-verify -n payment-prod get pods
runcmd kubectl --insecure-skip-tls-verify get nodes
note "k8sgpt v0.4.36 — 14 Go analyzers, no LLM involved"
runcmd k8sgpt analyze --kubecontext mock-context --no-cache -n payment-prod
''',
    },
    "explain": {
        "title": "k8sgpt --explain — each finding sent to an LLM",
        "script": r'''
note "same scan, now with --explain: every finding goes to the configured model"
runcmd curl -s http://127.0.0.1:8081/
runcmd k8sgpt analyze --kubecontext mock-context --no-cache -n payment-prod --explain --backend customrest --filter Node
''',
    },
    "remediate": {
        "title": "Real kubectl remediation — findings actually drop",
        "script": r'''
note "before: count the findings"
runcmd findings
note "now fix them with real kubectl writes against the API"
runcmd ./scripts/remediate.sh
note "after: the same command, against the state those writes produced"
runcmd findings
runcmd health
''',
    },
    "triage": {
        "title": "Alert → cluster context → LLM → Slack card",
        "script": r'''
note "a reference implementation of Robusta's flow (not Robusta itself — see kind/)"
runcmd python3 scripts/alert_triage_agent.py
''',
    },
}


def record(name, title, script, cast_path):
    """Run `script` in a real pty and write an asciinema v2 cast of the output."""
    body = PRELUDE + script
    env = dict(os.environ)
    env.update({
        "TERM": "xterm-256color",
        "COLUMNS": str(COLS), "LINES": str(ROWS),
        "KUBECONFIG": str(paths.KUBECONFIG),
        "PYTHONUNBUFFERED": "1",
    })

    events = []
    started = time.time()
    pid, fd = pty.fork()
    if pid == 0:  # child
        os.chdir(paths.ROOT)
        os.execvpe("bash", ["bash", "--noprofile", "--norc", "-c", body], env)

    try:
        import fcntl
        import struct
        import termios
        fcntl.ioctl(fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", ROWS, COLS, 0, 0))
    except Exception:
        pass

    while True:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            events.append([round(time.time() - started, 6), "o",
                           chunk.decode("utf-8", errors="replace")])
            sys.stdout.write(chunk.decode("utf-8", errors="replace"))
            sys.stdout.flush()
        else:
            finished, _ = os.waitpid(pid, os.WNOHANG)
            if finished:
                break
    try:
        os.close(fd)
    except OSError:
        pass

    duration = events[-1][0] if events else 0.0
    header = {
        "version": 2, "width": COLS, "height": ROWS,
        "timestamp": int(started), "title": title,
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
    }
    cast_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cast_path, "w") as handle:
        handle.write(json.dumps(header) + "\n")
        for event in events:
            handle.write(json.dumps(event) + "\n")
    print(f"\n[record] {cast_path}  ({len(events)} events, {duration:.1f}s)")
    return cast_path


def render_gif(cast_path, gif_path):
    """Render with the bundled Python renderer; use agg if it happens to exist."""
    gif_path.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("agg"):
        subprocess.run([
            "agg", str(cast_path), str(gif_path),
            "--font-family", "JetBrains Mono,DejaVu Sans Mono,monospace",
            "--theme", "monokai", "--idle-time-limit", "2",
        ], check=True)
    else:
        import cast_to_gif
        cast_to_gif.render(cast_path, gif_path)
    print(f"[record] {gif_path}  ({gif_path.stat().st_size / 1024:.0f} KiB)")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("which", nargs="?", default="all",
                        choices=sorted(DEMOS) + ["all"])
    parser.add_argument("--no-gif", action="store_true")
    args = parser.parse_args()

    names = sorted(DEMOS) if args.which == "all" else [args.which]
    for name in names:
        demo = DEMOS[name]
        print(f"\n{'=' * 70}\n[record] {name}: {demo['title']}\n{'=' * 70}")
        # Every recording starts from the pristine broken cluster so the GIFs
        # are reproducible and independent of each other.
        subprocess.run(["curl", "-sk", "-X", "POST",
                        f"{paths.MOCK_K8S_URL}/_demo/reset"],
                       capture_output=True)
        cast = record(name, demo["title"], demo["script"],
                      paths.RECORDING_DIR / f"{name}.cast")
        if not args.no_gif:
            render_gif(cast, paths.GIF_DIR / f"{name}.gif")


if __name__ == "__main__":
    main()
