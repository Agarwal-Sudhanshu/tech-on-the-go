"""Publish one daily episode to a static podcast site (RSS feed + index page).

    python publish/podcast.py --dir out/2026-11-15 --site site [--max-episodes 400]

Reads <dir>/brief.json and <dir>/voice.wav, encodes site/episodes/<date>.mp3,
upserts the episode in site/episodes.json (the source of truth) and regenerates
site/feed.xml and site/index.html from it. Standard library + ffmpeg only.
"""
import argparse
import datetime as dt
import email.utils
import html
import json
import os
import subprocess
import sys
import uuid
from urllib.parse import quote, urlparse
from xml.sax.saxutils import escape as _xesc

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BITRATE = "96k"           # mono speech; ~0.72 MB per minute
SAMPLE_RATE = "44100"
GUID_NS = uuid.UUID("6f3c1c5e-8a52-4d0e-9a2e-5d0b5f3e7a11")
PUB_HOUR = 7              # local time of day used for pubDate


def die(msg):
    sys.exit(f"podcast.py: error: {msg}")


def xesc(s):
    return _xesc(str(s), {'"': "&quot;", "'": "&apos;"})


def cdata(s):
    return "<![CDATA[" + str(s).replace("]]>", "]]]]><![CDATA[>") + "]]>"


def hesc(s):
    return html.escape(str(s), quote=True)


def safe_url(u):
    u = (u or "").strip()
    return u if urlparse(u).scheme in ("http", "https") and urlparse(u).netloc else ""


def run(cmd):
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        die(f"{cmd[0]} not found on PATH")
    except subprocess.CalledProcessError as e:
        die(f"{cmd[0]} failed:\n{e.stderr[-1500:]}")


def probe_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", path]).stdout.strip()
    return float(out)


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


# --------------------------------------------------------------- episode build

def pub_date(date_str, tzname):
    d = dt.datetime.strptime(date_str, "%Y-%m-%d")
    tz = dt.timezone.utc
    if ZoneInfo and tzname:
        try:
            tz = ZoneInfo(tzname)
        except Exception:
            pass
    return d.replace(hour=PUB_HOUR, tzinfo=tz)


def build_episode(brief, date_str, cfg):
    title = (brief.get("title") or "").strip() or f"Daily brief for {date_str}"
    stories = []
    for seg in brief.get("segments", []):
        if seg.get("kind") != "story":
            continue
        srcs = []
        for s in seg.get("sources") or []:
            url = safe_url(s.get("url"))
            if url:
                srcs.append({"name": (s.get("name") or urlparse(url).netloc).strip(), "url": url})
        stories.append({"headline": (seg.get("headline") or "").strip(),
                        "tag": (seg.get("tag") or "").strip(), "sources": srcs})
    desc = (brief.get("description") or "").strip()
    if not desc:
        heads = [s["headline"] for s in stories if s["headline"]]
        desc = ("In today's brief: " + "; ".join(heads) + ".") if heads else title
    tags = [t for t in (brief.get("tags") or []) if isinstance(t, str)]
    # show notes (HTML, built from escaped parts only)
    parts = [f"<p>{hesc(desc)}</p>"]
    if stories:
        parts.append("<p>Stories covered:</p><ul>")
        for s in stories:
            li = hesc(s["headline"])
            if s["sources"]:
                links = ", ".join(f'<a href="{hesc(x["url"])}">{hesc(x["name"])}</a>' for x in s["sources"])
                li += f" &mdash; Sources: {links}"
            parts.append(f"<li>{li}</li>")
        parts.append("</ul>")
    return {
        "date": date_str,
        "title": title,
        "description": desc,
        "show_notes_html": "".join(parts),
        "stories": stories,
        "tags": tags,
        "pub_date": pub_date(date_str, cfg.get("timezone")).isoformat(),
    }


def encode_mp3(wav, out, ep, cfg, cover):
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", wav]
    if cover:
        cmd += ["-i", cover, "-map", "0:a", "-map", "1:v",
                "-c:v", "mjpeg", "-vf", "scale=1400:1400", "-q:v", "4",
                "-disposition:v", "attached_pic",
                "-metadata:s:v", "title=Cover", "-metadata:s:v", "comment=Cover (front)"]
    cmd += ["-c:a", "libmp3lame", "-b:a", BITRATE, "-ac", "1", "-ar", SAMPLE_RATE,
            "-id3v2_version", "3", "-write_xing", "1",
            "-metadata", f"title={ep['title']}",
            "-metadata", f"artist={cfg.get('author') or cfg['channel_name']}",
            "-metadata", f"album={cfg['channel_name']}",
            "-metadata", f"date={ep['date']}",
            "-metadata", "genre=Podcast",
            "-metadata", f"comment={ep['description'][:500]}",
            out]
    run(cmd)


# ------------------------------------------------------------------- outputs

def abs_url(base, rel):
    return base + "/" + quote(rel)


def hhmmss(sec):
    sec = int(round(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def rfc2822(iso):
    return email.utils.format_datetime(dt.datetime.fromisoformat(iso))


def render_feed(eps, cfg, base, has_cover):
    pod = cfg.get("podcast", {})
    name = cfg["channel_name"]
    author = cfg.get("author") or name
    explicit = "true" if pod.get("explicit") else "false"
    desc = cfg.get("tagline") or name
    cat = pod.get("category") or "News"
    sub = pod.get("subcategory") or ""
    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
         'xmlns:content="http://purl.org/rss/1.0/modules/content/" '
         'xmlns:atom="http://www.w3.org/2005/Atom">',
         "<channel>",
         f"<title>{xesc(name)}</title>",
         f"<link>{xesc(base + '/')}</link>",
         f'<atom:link href="{xesc(base + "/feed.xml")}" rel="self" type="application/rss+xml"/>',
         f"<description>{xesc(desc)}</description>",
         f"<language>{xesc(cfg.get('language') or 'en')}</language>",
         f"<copyright>{xesc('© ' + str(dt.date.today().year) + ' ' + author)}</copyright>",
         "<generator>briefstudio podcast.py</generator>",
         f"<lastBuildDate>{email.utils.format_datetime(dt.datetime.now(dt.timezone.utc))}</lastBuildDate>",
         "<itunes:type>episodic</itunes:type>",
         f"<itunes:author>{xesc(author)}</itunes:author>",
         f"<itunes:summary>{xesc(desc)}</itunes:summary>",
         f"<itunes:explicit>{explicit}</itunes:explicit>"]
    if has_cover:
        img = base + "/cover.jpg"
        L += [f'<itunes:image href="{xesc(img)}"/>',
              f"<image><url>{xesc(img)}</url><title>{xesc(name)}</title><link>{xesc(base + '/')}</link></image>"]
    if sub:
        L.append(f'<itunes:category text="{xesc(cat)}"><itunes:category text="{xesc(sub)}"/></itunes:category>')
    else:
        L.append(f'<itunes:category text="{xesc(cat)}"/>')
    if pod.get("owner_email"):
        L.append(f"<itunes:owner><itunes:name>{xesc(author)}</itunes:name>"
                 f"<itunes:email>{xesc(pod['owner_email'])}</itunes:email></itunes:owner>")
    for e in eps:
        url = abs_url(base, e["mp3"])
        L += ["<item>",
              f"<title>{xesc(e['title'])}</title>",
              f"<link>{xesc(base + '/#' + e['date'])}</link>",
              f"<description>{cdata(e['show_notes_html'])}</description>",
              f"<content:encoded>{cdata(e['show_notes_html'])}</content:encoded>",
              f"<itunes:summary>{xesc(e['description'])}</itunes:summary>",
              f'<enclosure url="{xesc(url)}" length="{e["bytes"]}" type="audio/mpeg"/>',
              f'<guid isPermaLink="false">{xesc(e["guid"])}</guid>',
              f"<pubDate>{rfc2822(e['pub_date'])}</pubDate>",
              f"<itunes:duration>{hhmmss(e['duration'])}</itunes:duration>",
              f"<itunes:author>{xesc(author)}</itunes:author>",
              "<itunes:episodeType>full</itunes:episodeType>",
              f"<itunes:explicit>{explicit}</itunes:explicit>",
              "</item>"]
    L += ["</channel>", "</rss>", ""]
    return "\n".join(L)


CSS = """
:root{--bg1:#0b0f1f;--bg2:#141b3a;--ac:#6ea8ff;--tx:#f4f6fb;--mu:#9aa6c4;--card:rgba(255,255,255,.05);--bd:rgba(255,255,255,.12)}
*{box-sizing:border-box}
html{background:var(--bg1)}
body{margin:0;min-height:100vh;background:linear-gradient(180deg,var(--bg1),var(--bg2)) fixed;color:var(--tx);
font:16px/1.55 Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:32px 16px 64px}
header{margin-bottom:28px}
h1{font-size:clamp(28px,6vw,40px);line-height:1.15;margin:0 0 8px}
.tag{color:var(--mu);margin:0 0 16px}
.feed{display:inline-block;color:var(--bg1);background:var(--ac);padding:8px 16px;border-radius:999px;font-weight:700;text-decoration:none}
.feedurl{color:var(--mu);font-size:14px;margin-top:10px;word-break:break-all}
article{background:var(--card);border:1px solid var(--bd);border-radius:16px;padding:18px;margin:0 0 16px}
time{color:var(--ac);font-size:14px;font-weight:600;letter-spacing:.04em;text-transform:uppercase}
h2{font-size:20px;line-height:1.3;margin:4px 0 8px}
p{margin:0 0 10px;color:var(--mu)}
audio{width:100%;margin:6px 0 10px}
ul{margin:0;padding-left:20px}li{margin:4px 0}
a{color:var(--ac)}
footer{color:var(--mu);font-size:13px;margin-top:32px}
"""


def render_index(eps, cfg, base, has_cover):
    name = cfg["channel_name"]
    o = ["<!doctype html>", f'<html lang="{hesc(cfg.get("language") or "en")}"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f"<title>{hesc(name)}</title>",
         f'<meta name="description" content="{hesc(cfg.get("tagline", ""))}">',
         '<link rel="alternate" type="application/rss+xml" title="RSS" href="feed.xml">',
         f"<style>{CSS}</style></head><body><main><header>",
         f"<h1>{hesc(name)}</h1>",
         f'<p class="tag">{hesc(cfg.get("tagline", ""))}</p>',
         '<a class="feed" href="feed.xml">Subscribe via RSS</a>',
         f'<div class="feedurl">Feed: {hesc(base + "/feed.xml")}</div></header>']
    for e in eps:
        o.append(f'<article id="{hesc(e["date"])}"><time datetime="{hesc(e["date"])}">'
                 f'{hesc(dt.date.fromisoformat(e["date"]).strftime("%A, %d %B %Y"))}</time>'
                 f'<h2>{hesc(e["title"])}</h2>'
                 f'<audio controls preload="none" src="{hesc(quote(e["mp3"]))}"></audio>')
        if e.get("stories"):
            o.append("<ul>")
            for s in e["stories"]:
                li = hesc(s["headline"])
                links = ", ".join(f'<a href="{hesc(x["url"])}" rel="noopener">{hesc(x["name"])}</a>'
                                  for x in s["sources"])
                o.append(f"<li>{li}" + (f" &mdash; {links}" if links else "") + "</li>")
            o.append("</ul>")
        else:
            o.append(f"<p>{hesc(e['description'])}</p>")
        o.append("</article>")
    if not eps:
        o.append("<p>No episodes yet.</p>")
    o.append("</main></body></html>\n")
    return "\n".join(o)


# ----------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="per-day folder with brief.json and voice.wav")
    ap.add_argument("--site", required=True, help="site output folder")
    ap.add_argument("--max-episodes", type=int, default=400)
    ap.add_argument("--base-url", default="", help="override podcast.base_url from config")
    ap.add_argument("--config", default=os.path.join(ROOT, "config", "channel.json"))
    a = ap.parse_args()

    cfg = load_json(a.config)
    if not cfg:
        die(f"cannot read {a.config}")
    base = (a.base_url or cfg.get("podcast", {}).get("base_url") or "").strip().rstrip("/")
    if not base:
        die("podcast.base_url is empty in config/channel.json. Set it to the public site URL "
            "(e.g. https://<user>.github.io/<repo>) or pass --base-url.")
    if not safe_url(base):
        die(f"base URL must start with http:// or https:// (got {base!r})")
    if not cfg.get("channel_name"):
        die("channel_name missing in config")

    brief = load_json(os.path.join(a.dir, "brief.json"))
    wav = os.path.join(a.dir, "voice.wav")
    if brief is None or not os.path.exists(wav):
        die(f"{a.dir} needs brief.json and voice.wav")
    date_str = brief.get("date")
    try:
        dt.datetime.strptime(date_str or "", "%Y-%m-%d")
    except ValueError:
        die(f"brief.json date must be YYYY-MM-DD (got {date_str!r})")

    os.makedirs(os.path.join(a.site, "episodes"), exist_ok=True)
    cover = os.path.join(a.site, "cover.jpg")
    has_cover = os.path.exists(cover)

    ep = build_episode(brief, date_str, cfg)
    rel = f"episodes/{date_str}.mp3"
    out = os.path.join(a.site, rel)
    encode_mp3(wav, out, ep, cfg, cover if has_cover else None)

    index_path = os.path.join(a.site, "episodes.json")
    db = load_json(index_path, {"episodes": []})
    old = {e["date"]: e for e in db.get("episodes", [])}
    ep.update({"mp3": rel, "bytes": os.path.getsize(out),
               "duration": round(probe_duration(out), 2),
               "guid": old.get(date_str, {}).get("guid") or str(uuid.uuid5(GUID_NS, f"{cfg['channel_name']}|{date_str}"))})
    old[date_str] = ep
    eps = sorted(old.values(), key=lambda e: e["date"], reverse=True)
    for dropped in eps[a.max_episodes:]:
        try:
            os.remove(os.path.join(a.site, dropped["mp3"]))
        except OSError:
            pass
    eps = eps[:a.max_episodes]

    write_atomic(index_path, json.dumps({"episodes": eps}, indent=2, ensure_ascii=False) + "\n")
    write_atomic(os.path.join(a.site, "feed.xml"), render_feed(eps, cfg, base, has_cover))
    write_atomic(os.path.join(a.site, "index.html"), render_index(eps, cfg, base, has_cover))
    print(f"episode {date_str}: {ep['bytes']} bytes, {ep['duration']}s; {len(eps)} episode(s) in feed")
    if not cfg.get("podcast", {}).get("owner_email"):
        print("warning: podcast.owner_email is empty; Spotify needs it to verify ownership", file=sys.stderr)
    if not has_cover:
        print("warning: site/cover.jpg missing; run publish/cover.py --out site/cover.jpg", file=sys.stderr)


if __name__ == "__main__":
    main()
