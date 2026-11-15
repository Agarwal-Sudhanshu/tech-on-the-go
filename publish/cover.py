"""Square podcast cover art in the same style as the video slides.

    python publish/cover.py --out site/cover.jpg

Apple Podcasts wants a square JPEG/PNG, RGB, between 1400x1400 and 3000x3000
pixels (file size should stay under ~500 KB, a widely quoted limit). Spotify
accepts the same artwork. We render at 3000x3000 and lower JPEG quality until
the file is under the limit, falling back to 2400/1400 px if needed.
"""
import argparse
import glob
import json
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

BG_TOP, BG_BOTTOM = (9, 13, 28), (20, 26, 58)
ACCENT = (110, 168, 255)
ACCENT2 = (155, 123, 255)
TEXT = (244, 246, 251)
MUTED = (154, 166, 196)
MAX_BYTES = 500 * 1024
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def font(weight, size):
    for pat in (f"/usr/share/fonts/opentype/inter/Inter-{weight}.otf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        hits = glob.glob(pat)
        if hits:
            return ImageFont.truetype(hits[0], size)
    return ImageFont.load_default()


def background(S):
    t = np.linspace(0, 1, S)[:, None, None]
    img = np.repeat(np.array(BG_TOP) * (1 - t) + np.array(BG_BOTTOM) * t, S, axis=1)
    yy, xx = np.mgrid[0:S, 0:S]
    for (cx, cy, r, col, k) in ((0.88, 0.10, 0.80, ACCENT, 0.26), (0.05, 1.0, 0.70, ACCENT2, 0.16)):
        d = np.sqrt((xx - cx * S) ** 2 + (yy - cy * S) ** 2) / (r * S)
        glow = np.clip(1 - d, 0, 1) ** 2 * k
        img = img * (1 - glow[..., None]) + np.array(col) * glow[..., None]
    return Image.fromarray(img.astype(np.uint8)).convert("RGB")


def wrap(draw, text, fnt, max_w):
    lines, cur = [], ""
    for w in text.split():
        trial = f"{cur} {w}".strip()
        if cur and draw.textlength(trial, font=fnt) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def balanced_lines(draw, text, fnt, max_w):
    """Wrap, then try narrower widths to even out line lengths at same line count."""
    best = wrap(draw, text, fnt, max_w)
    n = len(best)
    w = max_w
    while w > max_w * 0.5:
        w -= max_w * 0.03
        cand = wrap(draw, text, fnt, w)
        if len(cand) != n:
            break
        best = cand
    return best


def render(S, name, tagline):
    img = background(S)
    d = ImageDraw.Draw(img)
    m = int(S * 0.09)
    max_w = S - 2 * m

    # Channel name: biggest size that fits in <=3 lines.
    for size in range(int(S * 0.17), int(S * 0.07), -int(S * 0.005)):
        f = font("Bold", size)
        lines = balanced_lines(d, name, f, max_w)
        if len(lines) <= 3 and len(lines) * size * 1.08 <= S * 0.52 and all(d.textlength(l, font=f) <= max_w for l in lines):
            break
    lh = int(size * 1.08)
    block_h = lh * len(lines)

    tf = font("Medium", int(S * 0.04))
    tlines = wrap(d, tagline, tf, max_w * 0.9) if tagline else []
    tlh = int(S * 0.056)
    tag_h = tlh * len(tlines)

    bar_h = int(S * 0.012)
    gap1, gap2 = int(S * 0.05), int(S * 0.05)
    total = bar_h + gap1 + block_h + (gap2 + tag_h if tlines else 0)
    y = (S - total) // 2

    # accent bar + dot, echoing the pill dot on the slides
    d.rounded_rectangle((m, y, m + int(S * 0.13), y + bar_h), radius=bar_h // 2, fill=ACCENT)
    r = int(S * 0.016)
    cx = m + int(S * 0.13) + int(S * 0.03)
    d.ellipse((cx - r, y + bar_h // 2 - r, cx + r, y + bar_h // 2 + r), fill=ACCENT2)
    y += bar_h + gap1

    f = font("Bold", size)
    for l in lines:
        d.text((m, y - int(size * 0.08)), l, font=f, fill=TEXT)
        y += lh
    y += gap2 - int(size * 0.08) if tlines else 0
    for l in tlines:
        d.text((m, y), l, font=tf, fill=MUTED)
        y += tlh
    return img


def save_under_limit(img_full, path):
    for size in (3000, 2400, 1400):
        img = img_full if size == img_full.width else img_full.resize((size, size), Image.LANCZOS)
        for q in (92, 88, 84, 80, 75, 70, 62, 55):
            img.save(path, "JPEG", quality=q, optimize=True, progressive=False, subsampling=0 if q >= 88 else 2)
            if os.path.getsize(path) <= MAX_BYTES:
                return size, q
    return size, q


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site/cover.jpg")
    ap.add_argument("--config", default=os.path.join(ROOT, "config", "channel.json"))
    ap.add_argument("--name")
    ap.add_argument("--tagline")
    a = ap.parse_args()
    cfg = {}
    if os.path.exists(a.config):
        with open(a.config, encoding="utf-8") as fh:
            cfg = json.load(fh)
    name = a.name or cfg.get("channel_name") or "Daily Brief"
    tagline = a.tagline if a.tagline is not None else cfg.get("tagline", "")
    img = render(3000, name, tagline)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    size, q = save_under_limit(img, a.out)
    print(f"wrote {a.out}: {size}x{size}, quality {q}, {os.path.getsize(a.out) // 1024} KB")


if __name__ == "__main__":
    main()
