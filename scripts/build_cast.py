"""
Build a "demo playback" script for asciinema that types commands slowly
and displays pre-captured real outputs.

This lets us produce clean, fast GIFs where the displayed output is 100% real
(actually produced by k8sgpt + GLM), but the typing speed and pauses are
controlled so the GIF is short and watchable.
"""
import json
import os
import subprocess
import time
import sys
import shlex

CAPTURE_DIR = "/home/z/my-project/captured"

# Each demo step is a dict with:
#   cmd:    the shell command to type
#   out:    the captured output to display after pressing Enter
#   pre_delay:    seconds to wait BEFORE typing the command
#   type_delay:   seconds per character while typing
#   post_delay:   seconds to wait after output before next step


def type_string(stream, s, delay=0.025):
    """Type a string one character at a time, writing to the stream."""
    for ch in s:
        stream.write(ch)
        stream.flush()
        time.sleep(delay)


def emit_output(stream, output, line_delay=0.05):
    """Emit pre-captured output, with a tiny delay between lines."""
    # First, send the Enter that the user would press after typing the command
    stream.write("\r\n")
    stream.flush()
    time.sleep(0.15)
    for line in output.split("\n"):
        stream.write(line + "\r\n")
        stream.flush()
        time.sleep(line_delay)
    time.sleep(0.4)


def build_demo_script(steps):
    """
    Convert a list of step dicts into an asciinema v2 cast (header + event stream).
    Each event is [time, "o", text] for output, or [time, "i", text] for input.
    """
    events = []
    t = 0.0
    # We type the command as INPUT events, then emit the captured output as OUTPUT events.
    for step in steps:
        pre = step.get("pre_delay", 0.4)
        t += pre
        # Send command as input (it will be echoed back as output by the terminal)
        cmd = step["cmd"]
        type_delay = step.get("type_delay", 0.04)
        for ch in cmd:
            t += type_delay
            events.append([round(t, 6), "o", ch])  # the terminal echoes typed chars
        # Press enter
        t += 0.1
        events.append([round(t, 6), "o", "\r\n"])
        # Now the captured output
        out = step.get("out", "")
        # split output into chunks for natural pacing
        chunk_size = 80
        out_delay = step.get("out_delay", 0.008)
        for i in range(0, len(out), chunk_size):
            t += out_delay
            chunk = out[i:i + chunk_size]
            events.append([round(t, 6), "o", chunk])
        # short pause between commands
        t += step.get("post_delay", 0.6)
    return events


def main():
    demo_name = sys.argv[1] if len(sys.argv) > 1 else "k8sgpt_scan"
    steps_file = os.path.join(CAPTURE_DIR, f"{demo_name}_steps.json")
    cast_file = os.path.join(CAPTURE_DIR, f"{demo_name}.cast")

    with open(steps_file) as f:
        steps = json.load(f)

    events = build_demo_script(steps)

    cast = {
        "version": 2,
        "width": 120,
        "height": 32,
        "timestamp": int(time.time()),
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
    }
    with open(cast_file, "w") as f:
        f.write(json.dumps(cast) + "\n")
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    print(f"Wrote cast: {cast_file} ({len(events)} events)")


if __name__ == "__main__":
    main()
