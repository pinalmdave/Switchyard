"""Generate the GitHub social preview card (1280x640 PNG).

Run: python scripts/make_social_preview.py
Output: docs/social-preview.png  (upload via Settings -> General -> Social preview)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 640
BG = (13, 17, 23)  # GitHub dark
PANEL = (22, 27, 34)
FG = (230, 237, 243)
MUTED = (139, 148, 158)
GREEN = (63, 185, 80)
RED = (248, 81, 73)
ACCENT = (88, 166, 255)

FONTS = Path("C:/Windows/Fonts")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Left accent rail
    d.rectangle([0, 0, 12, H], fill=ACCENT)

    title = font("arialbd.ttf", 96)
    tag = font("arial.ttf", 36)
    small = font("arial.ttf", 26)
    mono = font("consola.ttf", 26)
    mono_b = font("consolab.ttf", 26)

    # Title
    d.text((80, 70), "Switchyard", font=title, fill=FG)
    # Tagline (two lines)
    d.text((84, 196), "Catch it when Claude silently downgrades", font=tag, fill=MUTED)
    d.text((84, 244), "Fable 5 -> Opus 4.8 on your security work.", font=tag, fill=MUTED)

    # Mini terminal panel
    px0, py0, px1, py1 = 84, 330, W - 80, 560
    d.rounded_rectangle([px0, py0, px1, py1], radius=14, fill=PANEL)
    # window dots
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([px0 + 24 + i * 28, py0 + 22, px0 + 38 + i * 28, py0 + 36], fill=c)

    lines = [
        (GREEN, "$ ", FG, "switchyard report"),
        (MUTED, "  Total requests        60", None, ""),
        (MUTED, "  Fallback rate       ", RED, "5.00%"),
        (MUTED, "  Privacy mode          hash  ", GREEN, "(prompts never leave your machine)"),
    ]
    y = py0 + 70
    for c1, t1, c2, t2 in lines:
        x = px0 + 28
        d.text((x, y), t1, font=mono_b if t1.startswith("$") else mono, fill=c1)
        if c2 is not None:
            x += d.textlength(t1, font=mono)
            d.text((x, y), t2, font=mono_b, fill=c2)
        y += 40

    # Footer badges row
    d.text((84, 588), "MIT  -  local & tamper-evident  -  Claude Code plugin  -  pip install switchyard-ai",
            font=small, fill=MUTED)

    out = Path("docs/social-preview.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(f"wrote {out} ({W}x{H})")


if __name__ == "__main__":
    main()
