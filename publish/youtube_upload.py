#!/usr/bin/env python3
"""Upload a day's finished video to YouTube (Data API v3, standard library only).

Usage:
    python publish/youtube_upload.py --dir out/2026-11-15 [--privacy private|unlisted|public]
                                     [--dry-run] [--force]

Env (not needed for --dry-run): YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
PLAYLIST_URL = "https://www.googleapis.com/youtube/v3/playlistItems"

TITLE_MAX = 100            # characters (API docs: snippet.title)
DESC_MAX_BYTES = 5000      # bytes, not characters (API docs: snippet.description)
TAGS_MAX_CHARS = 500       # combined, commas count, tags with spaces count +2 for quotes
MIN_CHAPTER_SECONDS = 10
MIN_CHAPTERS = 3

AI_DISCLOSURE = ("The narration is an AI-generated clone of the host's own voice, "
                 "and the script is AI-assisted.")


class UploadError(Exception):
    pass


# ----------------------------------------------------------------- text helpers
def clean(text):
    """The API rejects < and > in title/description."""
    return str(text).replace("<", "").replace(">", "")


def truncate_chars(text, limit):
    text = " ".join(clean(text).split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut[limit // 2:]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:-") + "…"


def truncate_bytes(text, limit):
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", errors="ignore")


def fmt_ts(seconds):
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


# ------------------------------------------------------------------- chapters
def build_chapters(candidates, duration,
                   min_len=MIN_CHAPTER_SECONDS, min_count=MIN_CHAPTERS):
    """candidates: list of (start_seconds, title) in time order.

    Returns a list of (int_start, title) satisfying YouTube's rules (first at 0:00,
    >= min_count chapters, each >= min_len seconds), or [] if they cannot be met.
    A too-short first chapter absorbs the following one (taking its title, which is
    usually the more informative); any other too-short chapter is dropped so the
    previous chapter simply runs longer.
    """
    ch = [(int(s), t) for s, t in sorted(candidates, key=lambda c: c[0])]
    if not ch:
        return []
    ch[0] = (0, ch[0][1])
    end = int(duration)
    while True:
        short = None
        for i, (s, _t) in enumerate(ch):
            nxt = ch[i + 1][0] if i + 1 < len(ch) else end
            if nxt - s < min_len:
                short = i
                break
        if short is None:
            break
        if len(ch) == 1:
            return []
        if short == 0:
            ch[0] = (0, ch[1][1])
            del ch[1]
        else:
            del ch[short]
    return ch if len(ch) >= min_count else []


def chapter_candidates(brief, timings):
    segs = brief.get("segments") or []
    tsegs = timings.get("segments") or []
    out = []
    for i, ts in enumerate(tsegs):
        seg = segs[i] if i < len(segs) else {}
        kind = seg.get("kind")
        if kind == "story" and seg.get("headline"):
            title = seg["headline"]
        elif kind == "intro" or i == 0:
            title = "Intro"
        elif kind == "outro" or i == len(tsegs) - 1:
            title = "Wrap-up"
        else:
            title = seg.get("headline") or f"Part {i}"
        out.append((float(ts.get("start", 0)), truncate_chars(title, 100)))
    return out


# --------------------------------------------------------------------- metadata
def collect_sources(brief):
    seen, out = set(), []
    for seg in brief.get("segments") or []:
        for src in seg.get("sources") or []:
            if not isinstance(src, dict):
                continue
            url = (src.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(((src.get("name") or "").strip(), url))
    return out


def build_description(brief, timings, cfg=None):
    seo = ((cfg or {}).get("youtube") or {}).get("seo") or {}
    parts = []
    if brief.get("description"):
        parts.append(clean(brief["description"]).strip())

    chapters = build_chapters(chapter_candidates(brief, timings),
                              timings.get("duration", 0))
    if chapters:
        parts.append("\n".join(f"{fmt_ts(s)} {clean(t)}" for s, t in chapters))

    sources = collect_sources(brief)
    # Channel footer (links, what the show is) and hashtags; YouTube shows the first three hashtags above the title.
    tail = "\n\n".join(x for x in ("\n".join(seo.get("footer") or []), AI_DISCLOSURE,
                                   " ".join(seo.get("hashtags") or [])) if x)
    src_lines = [f"{clean(n)}: {u}" if n else u for n, u in sources]

    # Fit within the byte limit by dropping trailing source lines if needed.
    while True:
        blocks = list(parts)
        if src_lines:
            blocks.append("Sources\n" + "\n".join(src_lines))
        blocks.append(tail)
        text = "\n\n".join(blocks)
        if len(text.encode("utf-8")) <= DESC_MAX_BYTES or not src_lines:
            break
        src_lines.pop()
    return truncate_bytes(text, DESC_MAX_BYTES), chapters


def build_tags(brief, cfg=None):
    seo = ((cfg or {}).get("youtube") or {}).get("seo") or {}
    tags, total = [], 0
    # Story-specific tags first, then the channel's evergreen tags while space remains.
    for t in list(brief.get("tags") or []) + list(seo.get("evergreen_tags") or []):
        t = clean(t).replace(",", " ").strip()
        if not t or t in tags:
            continue
        cost = len(t) + (2 if " " in t else 0) + (1 if tags else 0)
        if total + cost > TAGS_MAX_CHARS:
            continue
        tags.append(t)
        total += cost
    return tags


def build_metadata(brief, timings, cfg, privacy):
    yt = cfg.get("youtube", {})
    title = brief.get("title") or f"{cfg.get('channel_name', 'Daily Brief')} {brief.get('date', '')}"
    # Add a searchable suffix ("| Tech News 16 Nov") when it fits.
    suffix = (yt.get("seo") or {}).get("title_suffix")
    if suffix and brief.get("date"):
        import datetime as _dt
        d = _dt.date.fromisoformat(brief["date"])
        cand = f"{title} | {suffix} {d.day} {d:%b %Y}"
        if len(cand) <= TITLE_MAX:
            title = cand
    description, chapters = build_description(brief, timings, cfg)
    snippet = {
        "title": truncate_chars(title, TITLE_MAX),
        "description": description,
        "categoryId": str(yt.get("category_id") or "28"),
    }
    tags = build_tags(brief, cfg)
    if tags:
        snippet["tags"] = tags
    if cfg.get("language"):
        snippet["defaultLanguage"] = cfg["language"]
    body = {
        "snippet": snippet,
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            # Documented field: realistic altered/synthetic content disclosure.
            "containsSyntheticMedia": True,
        },
    }
    return body, chapters


# ------------------------------------------------------------------------ HTTP
def api_error_text(raw):
    try:
        data = json.loads(raw)
    except Exception:
        return raw[:500].decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)[:500]
    err = data.get("error")
    if isinstance(err, dict):
        reasons = ", ".join(e.get("reason", "") for e in err.get("errors", []) if e.get("reason"))
        return f"{err.get('message', '')}" + (f" [{reasons}]" if reasons else "")
    if err:
        return f"{err}: {data.get('error_description', '')}"
    return str(data)[:500]


def _ssl_context():
    """python.org builds of Python on macOS ship without CA certificates; use the system bundle."""
    import ssl
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs():
        for bundle in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
            if os.path.exists(bundle):
                ctx.load_verify_locations(bundle)
                break
    return ctx


SSL_CTX = _ssl_context()


def http(req, timeout=300):
    """Return (status, headers, body). Raises UploadError on network failure."""
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()
    except (urllib.error.URLError, OSError) as e:
        raise UploadError(f"network error: {e}")


def get_access_token():
    missing = [k for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN") if not os.environ.get(k)]
    if missing:
        raise UploadError("missing environment variable(s): " + ", ".join(missing))
    data = urllib.parse.urlencode({
        "client_id": os.environ["YT_CLIENT_ID"],
        "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode()
    status, _h, body = http(urllib.request.Request(TOKEN_URL, data=data, method="POST"), 60)
    if status != 200:
        hint = ""
        if b"invalid_grant" in body:
            hint = (" (refresh token expired or revoked: if the OAuth consent screen is still in "
                    "'Testing', tokens expire after 7 days; see docs/youtube-setup.md)")
        raise UploadError(f"token refresh failed, HTTP {status}: {api_error_text(body)}{hint}")
    token = json.loads(body).get("access_token")
    if not token:
        raise UploadError("token endpoint returned no access_token")
    return token


def upload_video(token, body, video_path):
    size = video_path.stat().st_size
    meta = json.dumps(body).encode("utf-8")
    url = UPLOAD_URL + "?" + urllib.parse.urlencode(
        {"uploadType": "resumable", "part": "snippet,status"})
    req = urllib.request.Request(url, data=meta, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Length": str(size),
        "X-Upload-Content-Type": "video/mp4",
    })
    status, headers, resp = http(req, 120)
    if status != 200 or not headers.get("Location"):
        raise UploadError(f"could not start upload, HTTP {status}: {api_error_text(resp)}")
    session = headers["Location"]

    offset = 0
    for attempt in range(6):
        try:
            with open(video_path, "rb") as f:
                f.seek(offset)
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "video/mp4",
                    "Content-Length": str(size - offset),
                }
                if offset:
                    headers["Content-Range"] = f"bytes {offset}-{size - 1}/{size}"
                status, _h, resp = http(urllib.request.Request(
                    session, data=f, method="PUT", headers=headers), 1800)
        except UploadError as e:
            status, resp = None, str(e).encode()
        if status in (200, 201):
            return json.loads(resp)
        if status is not None and status not in (500, 502, 503, 504):
            raise UploadError(f"upload failed, HTTP {status}: {api_error_text(resp)}")
        # Resumable failure: ask the server how much it has, back off, retry.
        time.sleep(min(2 ** attempt, 30))
        try:
            st, h, r = http(urllib.request.Request(session, data=b"", method="PUT", headers={
                "Authorization": f"Bearer {token}",
                "Content-Length": "0",
                "Content-Range": f"bytes */{size}",
            }), 60)
        except UploadError:
            continue
        if st in (200, 201):
            return json.loads(r)
        if st == 308:
            rng = h.get("Range")
            offset = int(rng.split("-")[-1]) + 1 if rng else 0
        elif st == 404:
            raise UploadError("upload session expired; re-run to start over")
    raise UploadError("upload failed after repeated retries")


def add_to_playlist(token, playlist_id, video_id):
    body = json.dumps({"snippet": {
        "playlistId": playlist_id,
        "resourceId": {"kind": "youtube#video", "videoId": video_id},
    }}).encode()
    req = urllib.request.Request(PLAYLIST_URL + "?part=snippet", data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    })
    status, _h, resp = http(req, 60)
    if status != 200:
        raise UploadError(f"adding to playlist failed, HTTP {status}: {api_error_text(resp)}")


def set_thumbnail(token, video_id, image_path):
    """Set a custom thumbnail (the channel must have custom thumbnails enabled, i.e. be phone-verified)."""
    data = Path(image_path).read_bytes()
    url = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set?" + urllib.parse.urlencode(
        {"videoId": video_id, "uploadType": "media"})
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "image/jpeg",
        "Content-Length": str(len(data)),
    })
    status, _h, resp = http(req, 120)
    if status != 200:
        raise UploadError(f"setting thumbnail failed, HTTP {status}: {api_error_text(resp)}")


# ------------------------------------------------------------------------- main
def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise UploadError(f"missing file: {path}")
    except ValueError as e:
        raise UploadError(f"invalid JSON in {path}: {e}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="day folder containing brief.json, timings.json, video.mp4")
    ap.add_argument("--privacy", choices=["private", "unlisted", "public"])
    ap.add_argument("--dry-run", action="store_true", help="print metadata, make no network calls")
    ap.add_argument("--force", action="store_true", help="upload even if youtube.json already exists")
    ap.add_argument("--thumbnail-for", metavar="VIDEO_ID",
                    help="only set <dir>/thumbnail.jpg on an existing video, then exit")
    args = ap.parse_args(argv)

    try:
        d = Path(args.dir)
        if args.thumbnail_for:
            set_thumbnail(get_access_token(), args.thumbnail_for, d / "thumbnail.jpg")
            print(f"Thumbnail set on {args.thumbnail_for}")
            return 0
        record = d / "youtube.json"
        if record.exists() and not args.force and not args.dry_run:
            raise UploadError(f"{record} already exists (already uploaded); use --force to upload again")
        brief = load_json(d / "brief.json")
        timings = load_json(d / "timings.json")
        cfg = load_json(ROOT / "config" / "channel.json")
        video = d / "video.mp4"
        if not video.is_file():
            raise UploadError(f"missing file: {video}")

        privacy = args.privacy or cfg.get("youtube", {}).get("privacy") or "private"
        playlist_id = (cfg.get("youtube", {}).get("playlist_id") or "").strip()
        body, _chapters = build_metadata(brief, timings, cfg, privacy)

        if args.dry_run:
            print("DRY RUN: no network calls made.")
            print(f"video: {video} ({video.stat().st_size} bytes)")
            print(f"playlist: {playlist_id or '(none)'}")
            print("videos.insert body (part=snippet,status):")
            print(json.dumps(body, indent=2, ensure_ascii=False))
            print("\n--- description as shown on YouTube ---")
            print(body["snippet"]["description"])
            return 0

        token = get_access_token()
        result = upload_video(token, body, video)
        vid = result.get("id")
        if not vid:
            raise UploadError("upload finished but response had no video id")
        url = f"https://www.youtube.com/watch?v={vid}"
        record.write_text(json.dumps({"video_id": vid, "url": url, "privacy": privacy,
                                      "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                                     indent=2) + "\n")
        print(f"Uploaded: {vid}\n{url}")
        thumb = d / "thumbnail.jpg"
        if thumb.is_file():
            try:
                set_thumbnail(token, vid, thumb)
                print("Thumbnail set")
            except UploadError as e:  # never fail the episode over a thumbnail
                print(f"WARNING: video uploaded but {e}", file=sys.stderr)
        if playlist_id:
            try:
                add_to_playlist(token, playlist_id, vid)
                print(f"Added to playlist {playlist_id}")
            except UploadError as e:
                print(f"WARNING: video uploaded but {e}", file=sys.stderr)
                return 3
        return 0
    except UploadError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
