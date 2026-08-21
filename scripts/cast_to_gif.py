"""
Render an asciinema v2 cast to an animated GIF, with no external binaries.

The repo used to require the `agg` release binary to rebuild its GIFs, which
meant "reproduce the demos" involved a download from a third-party release
page. This renderer is pure Python + Pillow, so `./run.sh record` works on a
clean clone.

It implements the slice of terminal behaviour our demos actually emit: SGR
colour/bold/dim/reset, carriage return, newline, backspace, erase-in-line,
erase-in-display and basic cursor movement. It is not a VT100; it is enough
to faithfully replay the recorded bytes, and anything it cannot interpret is
dropped rather than drawn as garbage.

Usage:
    python scripts/cast_to_gif.py recordings/scan.cast gifs/scan.gif
"""
import argparse
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Monokai-ish, matching what the recordings were captured under.
PALETTE = {
    0: (39, 40, 34), 1: (249, 38, 114), 2: (166, 226, 46), 3: (244, 191, 117),
    4: (102, 217, 239), 5: (174, 129, 255), 6: (161, 239, 228), 7: (248, 248, 242),
    8: (117, 113, 94), 9: (249, 38, 114), 10: (166, 226, 46), 11: (244, 191, 117),
    12: (102, 217, 239), 13: (174, 129, 255), 14: (161, 239, 228), 15: (249, 248, 245),
}
BG = PALETTE[0]
FG = PALETTE[7]

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]
BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
]

CSI = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])")
OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


class Cell:
    __slots__ = ("ch", "fg", "bold", "dim")

    def __init__(self, ch=" ", fg=FG, bold=False, dim=False):
        self.ch, self.fg, self.bold, self.dim = ch, fg, bold, dim


class Screen:
    """A tiny terminal grid: enough VT to replay our own recordings."""

    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.grid = [[Cell() for _ in range(cols)] for _ in range(rows)]
        self.x = self.y = 0
        self.fg, self.bold, self.dim = FG, False, False

    def _newline(self):
        self.y += 1
        if self.y >= self.rows:
            self.grid.pop(0)
            self.grid.append([Cell() for _ in range(self.cols)])
            self.y = self.rows - 1

    def write(self, text):
        text = OSC.sub("", text)
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\x1b":
                match = CSI.match(text, i)
                if match:
                    self._csi(match.group(1), match.group(2))
                    i = match.end()
                    continue
                i += 2  # skip an escape we don't model
                continue
            if ch == "\r":
                self.x = 0
            elif ch == "\n":
                self.x = 0
                self._newline()
            elif ch == "\b":
                self.x = max(0, self.x - 1)
            elif ch == "\t":
                self.x = min(self.cols - 1, (self.x // 8 + 1) * 8)
            elif ch >= " ":
                if self.x >= self.cols:
                    self.x = 0
                    self._newline()
                self.grid[self.y][self.x] = Cell(ch, self.fg, self.bold, self.dim)
                self.x += 1
            i += 1

    def _csi(self, params, final):
        args = [int(p) for p in params.split(";") if p.isdigit()]
        if final == "m":
            self._sgr(args or [0])
        elif final == "K":
            mode = args[0] if args else 0
            start, end = (self.x, self.cols) if mode == 0 else (
                (0, self.x + 1) if mode == 1 else (0, self.cols))
            for col in range(start, end):
                self.grid[self.y][col] = Cell()
        elif final == "J":
            for row in range(self.rows):
                for col in range(self.cols):
                    self.grid[row][col] = Cell()
            self.x = self.y = 0
        elif final == "H":
            self.y = min(self.rows - 1, max(0, (args[0] if args else 1) - 1))
            self.x = min(self.cols - 1, max(0, (args[1] if len(args) > 1 else 1) - 1))
        elif final in "ABCD":
            step = args[0] if args else 1
            if final == "A":
                self.y = max(0, self.y - step)
            elif final == "B":
                self.y = min(self.rows - 1, self.y + step)
            elif final == "C":
                self.x = min(self.cols - 1, self.x + step)
            else:
                self.x = max(0, self.x - step)

    def _sgr(self, args):
        index = 0
        while index < len(args):
            code = args[index]
            if code == 0:
                self.fg, self.bold, self.dim = FG, False, False
            elif code == 1:
                self.bold = True
            elif code == 2:
                self.dim = True
            elif code == 22:
                self.bold = self.dim = False
            elif 30 <= code <= 37:
                self.fg = PALETTE[code - 30]
            elif 90 <= code <= 97:
                self.fg = PALETTE[code - 90 + 8]
            elif code == 39:
                self.fg = FG
            elif code == 38 and index + 2 < len(args) and args[index + 1] == 5:
                self.fg = PALETTE.get(args[index + 2] % 16, FG)
                index += 2
            index += 1


def load_font(size):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def load_bold(size):
    for path in BOLD_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return load_font(size)


def render(cast_path, gif_path, font_size=15, fps=10, max_seconds=None,
           idle_limit=1.5, tail_hold=2.5, min_gap=0.10):
    lines = Path(cast_path).read_text().splitlines()
    header = json.loads(lines[0])
    cols, rows = header.get("width", 100), header.get("height", 30)
    events = [json.loads(l) for l in lines[1:] if l.strip()]
    events = [e for e in events if e[1] == "o"]
    if not events:
        raise SystemExit(f"{cast_path} has no output events")

    # Collapse long idle gaps so the GIF stays watchable and small.
    # Long idle gaps are clamped so the GIF stays watchable and small; a
    # minimum gap is enforced so a burst of output doesn't flash past in one
    # frame. Timings are compressed, never fabricated: the order and the
    # content are exactly what the pty produced.
    squeezed, clock, previous = [], 0.0, 0.0
    for timestamp, _, data in events:
        gap = min(max(timestamp - previous, min_gap), idle_limit)
        clock += gap
        squeezed.append((clock, data))
        previous = timestamp
    total = squeezed[-1][0] + tail_hold
    if max_seconds:
        total = min(total, max_seconds)

    font, bold_font = load_font(font_size), load_bold(font_size)
    bbox = font.getbbox("M")
    cell_w = max(1, bbox[2] - bbox[0])
    cell_h = int(font_size * 1.35)
    pad = 12
    width = cols * cell_w + pad * 2
    height = rows * cell_h + pad * 2

    screen = Screen(cols, rows)
    frames, durations = [], []
    step = 1.0 / fps
    cursor = 0
    time_at = 0.0

    while time_at <= total:
        while cursor < len(squeezed) and squeezed[cursor][0] <= time_at:
            screen.write(squeezed[cursor][1])
            cursor += 1
        image = Image.new("RGB", (width, height), BG)
        draw = ImageDraw.Draw(image)
        for row_index, row in enumerate(screen.grid):
            y = pad + row_index * cell_h
            run_text, run_x, run_style = "", None, None
            for col_index, cell in enumerate(row):
                style = (cell.fg, cell.bold, cell.dim)
                if style != run_style and run_text:
                    _draw_run(draw, run_text, run_x, y, run_style, font, bold_font)
                    run_text, run_x = "", None
                if run_x is None:
                    run_x = pad + col_index * cell_w
                run_style = style
                run_text += cell.ch
            if run_text.strip():
                _draw_run(draw, run_text, run_x, y, run_style, font, bold_font)
        frames.append(image.quantize(colors=64, method=Image.MEDIANCUT))
        durations.append(int(step * 1000))
        time_at += step

    Path(gif_path).parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(gif_path, save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=True, disposal=2)
    return gif_path


def _draw_run(draw, text, x, y, style, font, bold_font):
    colour, bold, dim = style
    if dim:
        colour = tuple(int(c * 0.55) for c in colour)
    draw.text((x, y), text, font=bold_font if bold else font, fill=colour)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cast")
    parser.add_argument("gif")
    parser.add_argument("--font-size", type=int, default=15)
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument("--max-seconds", type=float, default=None)
    args = parser.parse_args()
    out = render(args.cast, args.gif, args.font_size, args.fps, args.max_seconds)
    print(f"wrote {out} ({Path(out).stat().st_size / 1024:.0f} KiB)")


if __name__ == "__main__":
    main()
