"""Brief script + narration -> 1080p video with headline slides, captions and waveform.

    render.py --brief out/demo/brief.json --timings out/demo/timings.json \
              --audio out/demo/voice.wav --out out/demo/video.mp4

With --frames-only it just writes the slide images (timings are estimated if
timings.json does not exist yet), which is handy for checking the design.
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1920, 1080
MARGIN = 140
BG_TOP, BG_BOTTOM = (9, 13, 28), (20, 26, 58)
ACCENT = (110, 168, 255)
ACCENT2 = (155, 123, 255)
TEXT = (244, 246, 251)
MUTED = (154, 166, 196)
WAVE_Y, WAVE_H = 925, 110


def font(weight, size):
    for pat in (f"/usr/share/fonts/opentype/inter/Inter-{weight}.otf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        hits = glob.glob(pat)
        if hits:
            return ImageFont.truetype(hits[0], size)
    return ImageFont.load_default()


def background():
    t = np.linspace(0, 1, H)[:, None, None]
    grad = np.array(BG_TOP) * (1 - t) + np.array(BG_BOTTOM) * t
    img = np.repeat(grad, W, axis=1)
    yy, xx = np.mgrid[0:H, 0:W]
    for (cx, cy, r, col, strength) in ((1650, 120, 760, ACCENT, 0.20), (150, 1050, 620, ACCENT2, 0.13)):
        d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / r
        glow = np.clip(1 - d, 0, 1) ** 2 * strength
        img = img * (1 - glow[..., None]) + np.array(col) * glow[..., None]
    return Image.fromarray(img.astype(np.uint8)).convert("RGBA")


def tracked(draw, xy, text, fnt, fill, tracking=0.0, anchor_right=False):
    """Draw letter-spaced text; returns its width."""
    widths = [draw.textlength(ch, font=fnt) + tracking for ch in text]
    total = sum(widths) - tracking
    x, y = xy
    if anchor_right:
        x -= total
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += w
    return total


def wrap(draw, text, fnt, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and draw.textlength(trial, font=fnt) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def fit_headline(draw, text, max_w, max_lines=3, sizes=(104, 92, 80, 70, 60)):
    for size in sizes:
        fnt = font("Bold", size)
        lines = wrap(draw, text, fnt, max_w)
        if len(lines) <= max_lines:
            break
    return fnt, lines, size


def chrome(img, brief):
    """Brand pill (top-left) and date (top-right), shared by every slide."""
    d = ImageDraw.Draw(img)
    label = brief["channel"].upper()
    f = font("SemiBold", 26)
    tw = sum(d.textlength(c, font=f) + 3 for c in label) - 3
    x0, y0, ph = MARGIN, 84, 58
    d.rounded_rectangle((x0, y0, x0 + tw + 86, y0 + ph), radius=ph // 2,
                        fill=(255, 255, 255, 20), outline=(255, 255, 255, 46), width=2)
    d.ellipse((x0 + 24, y0 + 21, x0 + 40, y0 + 37), fill=ACCENT)
    tracked(d, (x0 + 56, y0 + 13), label, f, TEXT, 3)
    date = dt.date.fromisoformat(brief["date"])
    nice = f"{date:%A}, {date.day} {date:%B %Y}"
    tracked(d, (W - MARGIN, y0 + 13), nice, font("Medium", 28), MUTED, 0.5, anchor_right=True)


def slide(bg, brief, seg, story_no, story_total):
    # Draw on a transparent layer so translucent shapes blend with the background.
    img = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    chrome(img, brief)
    d = ImageDraw.Draw(img)
    stories = [s for s in brief["segments"] if s["kind"] == "story"]
    max_w = W - 2 * MARGIN - 120

    if seg["kind"] == "intro":
        tracked(d, (MARGIN, 232), "TODAY'S CATCH-UP", font("SemiBold", 30), ACCENT, 6)
        # With more than three stories the list is set smaller so it stays clear of the captions.
        compact = len(stories) > 3
        d.text((MARGIN - 4, 276), "Today in " + str(len(stories)) + " stories",
               font=font("Bold", 92 if compact else 112), fill=TEXT)
        y = 404 if compact else 468
        # the list must end above the caption box (top ~720)
        step = min(60, (700 - 404) // len(stories)) if compact else 84
        hs = min(36, int(step * 0.62)) if compact else 44
        num_f, head_f = font("Bold", hs - 4 if compact else 40), font("Medium", hs)
        for i, s in enumerate(stories, 1):
            d.text((MARGIN, y + 2), f"{i:02d}", font=num_f, fill=ACCENT)
            line = wrap(d, s["headline"], head_f, max_w - 110)[0]
            if line != s["headline"]:
                line = line.rstrip(" ,.") + "…"
            d.text((MARGIN + 100, y), line, font=head_f, fill=TEXT)
            y += step
    elif seg["kind"] == "story":
        label = f"STORY {story_no} OF {story_total}"
        w = tracked(d, (MARGIN, 250), label, font("SemiBold", 30), ACCENT, 6)
        if seg.get("tag"):
            tracked(d, (MARGIN + w + 26, 250), "·  " + seg["tag"].upper(), font("SemiBold", 30), MUTED, 6)
        fnt, lines, size = fit_headline(d, seg["headline"], max_w)
        y = 312
        for line in lines:
            d.text((MARGIN - 3, y), line, font=fnt, fill=TEXT)
            y += int(size * 1.16)
        y += 34
        d.rounded_rectangle((MARGIN, y, MARGIN + 150, y + 8), radius=4, fill=ACCENT)
        # progress dots
        x = W - MARGIN - (story_total * 34 - 14)
        for i in range(1, story_total + 1):
            col = ACCENT if i <= story_no else (255, 255, 255, 60)
            d.ellipse((x, 258, x + 20, 278), fill=col)
            x += 34
    else:  # outro
        tracked(d, (MARGIN, 250), "THAT'S THE BRIEF", font("SemiBold", 30), ACCENT, 6)
        d.text((MARGIN - 4, 300), "Thanks for listening", font=font("Bold", 120), fill=TEXT)
        d.text((MARGIN, 470), "A new brief every day. Subscribe to stay current.",
               font=font("Medium", 46), fill=MUTED)
    return Image.alpha_composite(bg, img)


def on_screen(text):
    """Captions show written forms of spoken spellings ("A I" -> "AI", "F B I" -> "FBI")."""
    return re.sub(r"\b(?:[A-Z] )+[A-Z]\b", lambda m: m.group(0).replace(" ", ""), text)


def with_caption(base, text):
    if not text:
        return base
    text = on_screen(text)
    img = base.copy()
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    fnt = font("Medium", 46)
    lines = wrap(d, text, fnt, W - 2 * MARGIN - 160)[:2]
    lh = 62
    bw = max(d.textlength(l, font=fnt) for l in lines) + 96
    bh = lh * len(lines) + 44
    cx, bottom = W // 2, 890
    box = (cx - bw / 2, bottom - bh, cx + bw / 2, bottom)
    d.rounded_rectangle(box, radius=26, fill=(6, 9, 20, 170), outline=(255, 255, 255, 34), width=2)
    y = bottom - bh + 20
    for l in lines:
        d.text((cx - d.textlength(l, font=fnt) / 2, y), l, font=fnt, fill=TEXT)
        y += lh
    return Image.alpha_composite(img, layer)


def phrases(text, limit=78):
    """Break a passage into caption-sized phrases at natural pauses."""
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", text.strip()):
        parts = [sent]
        while any(len(p) > limit for p in parts):
            nxt = []
            for p in parts:
                if len(p) <= limit:
                    nxt.append(p)
                    continue
                mid = len(p) // 2
                commas = [m.end() for m in re.finditer(r",\s", p)]
                spaces = [m.end() for m in re.finditer(r"\s", p)]
                cut = min(commas or spaces, key=lambda i: abs(i - mid)) if (commas or spaces) else mid
                if commas and abs(cut - mid) > len(p) * 0.3 and spaces:
                    cut = min(spaces, key=lambda i: abs(i - mid))
                nxt += [p[:cut].strip(), p[cut:].strip()]
            parts = nxt
        out += [p for p in parts if p]
    return out


def estimate_timings(brief):
    segs, t = [], 0.0
    for i, s in enumerate(brief["segments"]):
        dur = len(s["text"]) / 15.0
        segs.append({"index": i, "start": t, "end": t + dur + 0.75, "speech_end": t + dur,
                     "chunks": [{"text": s["text"], "start": t, "end": t + dur}]})
        t += dur + 0.75
    return {"duration": t, "segments": segs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", required=True)
    ap.add_argument("--timings")
    ap.add_argument("--audio")
    ap.add_argument("--out")
    ap.add_argument("--frames-only", action="store_true")
    args = ap.parse_args()

    brief = json.load(open(args.brief, encoding="utf-8"))
    if args.timings and os.path.exists(args.timings):
        timings = json.load(open(args.timings))
    else:
        timings = estimate_timings(brief)

    work = os.path.join(os.path.dirname(args.brief), "frames")
    os.makedirs(work, exist_ok=True)
    for old in glob.glob(os.path.join(work, "*.png")):
        os.remove(old)

    bg = background()
    total_stories = sum(1 for s in brief["segments"] if s["kind"] == "story")
    cues, story_no = [], 0  # (start_time, base_slide, caption)
    for seg, st in zip(brief["segments"], timings["segments"]):
        if seg["kind"] == "story":
            story_no += 1
        base = slide(bg, brief, seg, story_no, total_stories)
        for ch in st["chunks"]:
            ps = phrases(ch["text"])
            total = sum(len(p) for p in ps)
            t = ch["start"]
            for p in ps:
                cues.append((t, base, p))
                t += (ch["end"] - ch["start"]) * len(p) / total
        cues.append((st["speech_end"] + 0.3, base, ""))  # clear caption in the pause

    lines = []
    for i, (start, base, cap) in enumerate(cues):
        end = cues[i + 1][0] if i + 1 < len(cues) else timings["duration"]
        path = os.path.abspath(os.path.join(work, f"f{i:04d}.png"))
        with_caption(base, cap).convert("RGB").save(path)
        lines += [f"file '{path}'", f"duration {max(end - start, 0.04):.3f}"]
    lines.append(f"file '{path}'")
    concat = os.path.join(work, "frames.txt")
    open(concat, "w").write("\n".join(lines) + "\n")
    print(f"{len(cues)} frames written to {work}")
    if args.frames_only:
        return

    wave = (f"[1:a]aformat=channel_layouts=mono,showwaves=s={W - 2 * MARGIN}x{WAVE_H}:mode=cline:draw=full:scale=sqrt:rate=30:"
            f"colors=0x6ea8ff,format=rgba,colorkey=black:0.08:0.0[w];"
            f"[0:v]fps=30,format=yuv420p[bg];[bg][w]overlay={MARGIN}:{WAVE_Y}[v]")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", concat, "-i", args.audio,
        "-filter_complex", wave, "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-t", f"{timings['duration']:.3f}",
        "-movflags", "+faststart", args.out,
    ], check=True)
    print(f"video written: {args.out}")


if __name__ == "__main__":
    main()
