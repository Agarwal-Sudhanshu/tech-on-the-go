"""Generate YouTube channel art in the same style as the video and podcast cover.

    python publish/branding.py --outdir out/branding

Writes avatar.png (800x800; YouTube crops it to a circle) and banner.png
(2560x1440; only the central 1546x423 strip is visible on every device, so
all text sits inside it).
"""
import argparse
import glob
import json
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

BG_TOP, BG_BOTTOM = (9, 13, 28), (20, 26, 58)
ACCENT, ACCENT2 = (110, 168, 255), (155, 123, 255)
TEXT, MUTED = (244, 246, 251), (154, 166, 196)


def font(weight, size):
    for pat in (f"/usr/share/fonts/opentype/inter/Inter-{weight}.otf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        hits = glob.glob(pat)
        if hits:
            return ImageFont.truetype(hits[0], size)
    return ImageFont.load_default()


def background(w, h, glows):
    t = np.linspace(0, 1, h)[:, None, None]
    img = np.repeat(np.array(BG_TOP) * (1 - t) + np.array(BG_BOTTOM) * t, w, axis=1)
    yy, xx = np.mgrid[0:h, 0:w]
    for cx, cy, r, col, strength in glows:
        g = np.clip(1 - np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / r, 0, 1) ** 2 * strength
        img = img * (1 - g[..., None]) + np.array(col) * g[..., None]
    return Image.fromarray(img.astype(np.uint8))


def tracked(d, x, y, text, fnt, fill, tracking):
    for ch in text:
        d.text((x, y), ch, font=fnt, fill=fill)
        x += d.textlength(ch, font=fnt) + tracking
    return x


def tracked_width(d, text, fnt, tracking):
    return sum(d.textlength(ch, font=fnt) + tracking for ch in text) - tracking


def avatar(size=800):
    img = background(size, size, [(size * 0.85, size * 0.1, size * 0.9, ACCENT, 0.28),
                                  (size * 0.1, size * 0.95, size * 0.8, ACCENT2, 0.20)])
    d = ImageDraw.Draw(img)
    f = font("Bold", int(size * 0.2))
    lines = ["Tech", "On The", "Go"]
    lh = int(size * 0.21)
    block_h = lh * len(lines)
    y = (size - block_h) // 2 + int(size * 0.035)
    # accent bar + dot above the wordmark, all inside the circular crop
    bar_w = int(size * 0.16)
    bx = (size - bar_w - int(size * 0.07)) // 2
    by = y - int(size * 0.075)
    d.rounded_rectangle((bx, by, bx + bar_w, by + int(size * 0.018)), radius=int(size * 0.009), fill=ACCENT)
    r = int(size * 0.022)
    d.ellipse((bx + bar_w + int(size * 0.03), by - r + int(size * 0.009),
               bx + bar_w + int(size * 0.03) + 2 * r, by + r + int(size * 0.009)), fill=ACCENT2)
    for line in lines:
        w = d.textlength(line, font=f)
        d.text(((size - w) / 2, y), line, font=f, fill=TEXT)
        y += lh
    return img


def banner(cfg, w=2560, h=1440):
    img = background(w, h, [(w * 0.82, h * 0.2, 1300, ACCENT, 0.24), (w * 0.12, h * 0.9, 1100, ACCENT2, 0.16)])
    d = ImageDraw.Draw(img)
    cx = w // 2
    top = (h - 423) // 2  # top of the always-visible strip
    name = cfg["channel_name"]
    f_name = font("Bold", 132)
    while d.textlength(name, font=f_name) > 1440:
        f_name = font("Bold", f_name.size - 6)
    f_tag, f_topics = font("Medium", 52), font("SemiBold", 30)
    y = top + 52
    d.text((cx - d.textlength(name, font=f_name) / 2, y), name, font=f_name, fill=TEXT)
    y += int(f_name.size * 1.22)
    tag = cfg.get("tagline", "")
    d.text((cx - d.textlength(tag, font=f_tag) / 2, y), tag, font=f_tag, fill=MUTED)
    y += 92
    d.rounded_rectangle((cx - 75, y, cx + 75, y + 8), radius=4, fill=ACCENT)
    y += 38
    topics = cfg.get("topics_line", "").upper()
    tw = tracked_width(d, topics, f_topics, 5)
    tracked(d, cx - tw / 2, y, topics, f_topics, ACCENT, 5)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="out/branding")
    args = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = json.load(open(os.path.join(root, "config", "channel.json"), encoding="utf-8"))
    os.makedirs(args.outdir, exist_ok=True)
    avatar().save(os.path.join(args.outdir, "avatar.png"))
    banner(cfg).save(os.path.join(args.outdir, "banner.png"), optimize=True)
    for n in ("avatar.png", "banner.png"):
        p = os.path.join(args.outdir, n)
        print(f"{p}: {os.path.getsize(p) // 1024} KB")


if __name__ == "__main__":
    main()
