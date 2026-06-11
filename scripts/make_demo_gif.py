"""Render the README demo as a self-contained animated GIF (no asciinema/agg).

Runs the real Switchyard CLI against an isolated, temporary ledger, captures the
output, and animates a terminal-style typing + reveal as docs/demo.gif.

Run:  python scripts/make_demo_gif.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# -- look -----------------------------------------------------------------------

SCALE = 2  # supersample then downscale for crisp text
COLS, ROWS = 78, 23
PAD = 24
CH_W, CH_H = 11, 26  # base cell size (before scale)
BG = (13, 17, 23)
PANEL = (22, 27, 34)
PROMPT = (63, 185, 80)
CMD = (230, 237, 243)
OUT = (160, 170, 182)
RED = (248, 81, 73)
GREEN = (86, 211, 100)
DOTS = [(255, 95, 86), (255, 189, 46), (39, 201, 63)]

FONTS = Path("C:/Windows/Fonts")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def color_for(line: str) -> tuple[int, int, int]:
    low = line.lower()
    if "fallback rate" in low or "fallback events" in low:
        return RED
    if "ok:" in low or "verified" in low or "never leave" in low or "reframe" in low:
        return GREEN
    return OUT


def run(args: list[str], home: str) -> list[str]:
    env = {**os.environ, "SWITCHYARD_HOME": home}
    out = subprocess.run(
        [sys.executable, "-m", "switchyard.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    text = ANSI.sub("", out.stdout + out.stderr)
    return [ln.rstrip() for ln in text.splitlines()]


# -- frame model ----------------------------------------------------------------


class Term:
    def __init__(self) -> None:
        self.buffer: list[tuple[str, tuple[int, int, int]]] = []
        self.frames: list[Image.Image] = []
        self.durations: list[int] = []
        self.font = ImageFont.truetype(str(FONTS / "consola.ttf"), CH_H * SCALE - 8)
        self.font_b = ImageFont.truetype(str(FONTS / "consolab.ttf"), CH_H * SCALE - 8)

    def _render(self) -> Image.Image:
        w = (COLS * CH_W + 2 * PAD) * SCALE
        h = (ROWS * CH_H + 2 * PAD + 18) * SCALE
        img = Image.new("RGB", (w, h), BG)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=16 * SCALE, fill=PANEL)
        for i, c in enumerate(DOTS):
            cx = (PAD + 10 + i * 26) * SCALE
            cy = (PAD - 2) * SCALE
            d.ellipse([cx, cy, cx + 14 * SCALE, cy + 14 * SCALE], fill=c)
        y = (PAD + 24) * SCALE
        for text, col in self.buffer[-ROWS:]:
            x = (PAD + 6) * SCALE
            if text.startswith("$ "):
                d.text((x, y), "$ ", font=self.font_b, fill=PROMPT)
                d.text((x + 2 * CH_W * SCALE, y), text[2:], font=self.font_b, fill=CMD)
            else:
                d.text((x, y), text, font=self.font, fill=col)
            y += CH_H * SCALE
        return img.resize((w // SCALE, h // SCALE), Image.LANCZOS)

    def snap(self, ms: int) -> None:
        self.frames.append(self._render())
        self.durations.append(ms)

    def type_command(self, cmd: str) -> None:
        # Reveal in a few chunks (not per-char) to keep the frame count small.
        step = max(1, len(cmd) // 4)
        for end in range(step, len(cmd), step):
            self.buffer.append(("$ " + cmd[:end], CMD))
            self.snap(70)
            self.buffer.pop()
        self.buffer.append(("$ " + cmd, CMD))
        self.snap(500)

    def emit(self, lines: list[str]) -> None:
        # Two lines per frame keeps long tables from ballooning the GIF.
        for i, ln in enumerate(lines):
            self.buffer.append((ln, color_for(ln)))
            if i % 2 == 1:
                self.snap(170)
        self.snap(1500)  # hold so viewers can read

    def blank(self) -> None:
        self.buffer.append(("", OUT))


def main() -> None:
    term = Term()
    with tempfile.TemporaryDirectory() as home:
        run(["demo", "--simulate"], home)  # seed off-screen

        for args, label in [
            (["report"], "switchyard report"),
            (["rescope", "exploit this binary"], 'switchyard rescope "exploit this binary"'),
            (["verify"], "switchyard verify"),
        ]:
            term.type_command(label)
            term.emit(run(args, home))
            term.blank()

    # Quantize every frame to ONE shared palette (terminal has few colors) so
    # the GIF stays small and doesn't flicker between per-frame palettes.
    palette = term.frames[-1].convert("P", palette=Image.ADAPTIVE, colors=64)
    quant = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in term.frames]

    out = Path("docs/demo.gif")
    out.parent.mkdir(parents=True, exist_ok=True)
    quant[0].save(
        out,
        save_all=True,
        append_images=quant[1:],
        duration=term.durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    kb = out.stat().st_size / 1024
    print(f"wrote {out} ({len(quant)} frames, {kb:.0f} KB)")


if __name__ == "__main__":
    main()
