"""Brief -> YouTube thumbnail (1280x720 JPEG) in the channel's visual style.

    thumbnail.py --brief out/2026-11-16/brief.json --out out/2026-11-16/thumbnail.jpg

The big text is the brief's `thumbnail_text` (a few punchy words about the top
story); if that is missing, the first story's headline is used.
"""
import argparse
import datetime as dt
import glob
import json

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H, M = 1280, 720, 64
BG_TOP, BG_BOTTOM = (9, 13, 28), (22, 28, 66)
ACCENT, ACCENT2 = (110, 168, 255), (155, 123, 255)
TEXT, MUTED = (255, 255, 255), (176, 187, 214)


def font(weight, size):
    for pat in (f"/usr/share/fonts/opentype/inter/Inter-{weight}.otf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        hits = glob.glob(pat)
        if hits:
            return ImageFont.truetype(hits[0], size)
    return ImageFont.load_default()


def background():
    t = np.linspace(0, 1, H)[:, None, None]
    img = np.repeat(np.array(BG_TOP) * (1 - t) + np.array(BG_BOTTOM) * t, W, axis=1)
    yy, xx = np.mgrid[0:H, 0:W]
    for cx, cy, r, col, strength in ((1180, 90, 620, ACCENT, 0.42), (80, 720, 520, ACCENT2, 0.30)):
        g = np.clip(1 - np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / r, 0, 1) ** 2 * strength
        img = img * (1 - g[..., None]) + np.array(col) * g[..., None]
    return Image.fromarray(img.astype(np.uint8)).convert("RGBA")


def tracked(d, x, y, text, fnt, fill, tracking):
    for ch in text:
        d.text((x, y), ch, font=fnt, fill=fill)
        x += d.textlength(ch, font=fnt) + tracking
    return x


def tracked_width(d, text, fnt, tracking):
    return sum(d.textlength(ch, font=fnt) + tracking for ch in text) - tracking


def wrap(d, text, fnt, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and d.textlength(trial, font=fnt) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def fit(d, text, max_w, max_h, max_lines=3):
    """Largest size at which the text fits the box in at most max_lines lines."""
    for size in range(190, 60, -6):
        fnt = font("ExtraBold", size)
        lines = wrap(d, text, fnt, max_w)
        if (len(lines) <= max_lines and len(lines) * size * 1.08 <= max_h
                and all(d.textlength(l, font=fnt) <= max_w for l in lines)):
            return fnt, lines, size
    fnt = font("ExtraBold", 66)
    return fnt, wrap(d, text, fnt, max_w)[:max_lines], 66


def render(brief):
    stories = [s for s in brief["segments"] if s["kind"] == "story"]
    big = (brief.get("thumbnail_text") or (stories[0]["headline"] if stories else brief["channel"])).strip()
    img = background()
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # brand pill (top-left) and date chip (top-right)
    brand = "TECH ON THE GO"
    f = font("Bold", 28)
    bw = tracked_width(d, brand, f, 3)
    d.rounded_rectangle((M, 48, M + bw + 84, 48 + 60), radius=30, fill=(255, 255, 255, 30),
                        outline=(255, 255, 255, 70), width=2)
    d.ellipse((M + 24, 48 + 21, M + 42, 48 + 39), fill=ACCENT)
    tracked(d, M + 58, 48 + 13, brand, f, TEXT, 3)
    date = dt.date.fromisoformat(brief["date"])
    chip = f"{date.day} {date:%b}".upper()
    cw = tracked_width(d, chip, f, 3)
    d.rounded_rectangle((W - M - cw - 52, 48, W - M, 48 + 60), radius=30, fill=ACCENT)
    tracked(d, W - M - cw - 26, 48 + 13, chip, f, (9, 13, 28), 3)

    # headline block
    top, bottom = 150, 560
    fnt, lines, size = fit(d, big, W - 2 * M, bottom - top)
    lh = int(size * 1.08)
    y = top + (bottom - top - lh * len(lines)) // 2
    for i, line in enumerate(lines):
        d.text((M - 4, y), line, font=fnt, fill=ACCENT if i == 0 and len(lines) > 1 else TEXT)
        y += lh

    # footer: accent bar, topic tag, "+N more stories"
    d.rounded_rectangle((M, 600, M + 120, 610), radius=5, fill=ACCENT)
    f2 = font("SemiBold", 34)
    x = M
    if stories and stories[0].get("tag"):
        x = tracked(d, x, 636, stories[0]["tag"].upper(), f2, ACCENT, 4) + 26
    if len(stories) > 1:
        tracked(d, x, 636, f"+ {len(stories) - 1} MORE STORIES TODAY", f2, MUTED, 2)
    return Image.alpha_composite(img, layer).convert("RGB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    brief = json.load(open(args.brief, encoding="utf-8"))
    render(brief).save(args.out, "JPEG", quality=90, optimize=True)
    print(f"thumbnail written: {args.out}")


if __name__ == "__main__":
    main()
