#!/usr/bin/env python3
"""Collect recent AI/IT news from configured feeds into <outdir>/news.json.

Standard library only. Usage:
    python pipeline/collect.py --outdir out/2026-11-15 [--hours 30] [--check]
"""
import argparse
import concurrent.futures as cf
import difflib
import hashlib
import html
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (compatible; NewsBriefBot/1.0; +feed-reader)"
TIMEOUT = 20
MAX_SUMMARY = 600

# Keywords used to refine the topic of general-interest sources.
TOPIC_KEYWORDS = [
    ("Breach", r"\b(data breach|breach(ed)?|data leak|leaked|leaks?|exposed (data|records)|stolen data|ransomware attack|hacked)\b"),
    ("Security", r"\b(vulnerabilit\w+|CVE-\d+|zero-day|0-day|exploit\w*|patch(es|ed)?|malware|backdoor|end[- ]of[- ]life|security (flaw|bug|update|advisory))\b"),
    ("Outage", r"\b(outage|downtime|went down|is down|service disruption|degraded)\b"),
    ("AI", r"\b(AI|LLM|GPT[- ]?\w*|Claude|Gemini|OpenAI|Anthropic|chatbot|machine learning|language model|Llama|Copilot)\b"),
]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def ssl_context():
    """Default context; fall back to the system CA bundle when Python ships none (python.org macOS builds)."""
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0) == 0:
        for p in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
            if Path(p).exists():
                return ssl.create_default_context(cafile=p)
    return ctx


SSL_CTX = ssl_context()


def fetch(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, application/json, text/xml, */*;q=0.5",
    })
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if attempt == 2 or e.code not in (429, 500, 502, 503, 504):
                raise
            time.sleep(2)


def strip_html(s):
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    dt = None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        pass
    if dt is None:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def child_text(el, *names):
    for c in el:
        if local(c.tag) in names and (c.text or "").strip():
            return c.text.strip()
    return ""


def parse_feed(data):
    """Parse RSS 2.0 / RSS 1.0 / Atom into [{title,url,published,summary}]."""
    root = ET.fromstring(data)
    items = []
    for el in root.iter():
        name = local(el.tag)
        if name not in ("item", "entry"):
            continue
        title = strip_html(child_text(el, "title"))
        link = ""
        if name == "entry":
            alt = None
            for c in el:
                if local(c.tag) == "link":
                    if c.get("rel", "alternate") == "alternate":
                        alt = c.get("href")
                        break
                    alt = alt or c.get("href")
            link = alt or ""
        else:
            link = child_text(el, "link")
            if not link:
                for c in el:
                    if local(c.tag) == "link" and c.get("href"):
                        link = c.get("href")
        date = parse_date(child_text(el, "pubDate", "published", "updated", "date"))
        summary = strip_html(child_text(el, "description", "summary", "encoded", "content"))
        if title and link:
            items.append({"title": title, "url": link.strip(), "published": date, "summary": summary})
    return items


def fetch_hn(src):
    ids = json.loads(fetch(src["url"]))[: int(src.get("limit", 40))]
    base = src["url"].rsplit("/", 1)[0]

    def one(i):
        try:
            return json.loads(fetch("%s/item/%s.json" % (base, i), timeout=10))
        except Exception:
            return None

    out = []
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        for it in ex.map(one, ids):
            if not it or it.get("type") != "story" or it.get("dead") or it.get("deleted"):
                continue
            if it.get("score", 0) < int(src.get("min_score", 0)):
                continue
            url = it.get("url") or "https://news.ycombinator.com/item?id=%s" % it["id"]
            out.append({
                "title": it.get("title", ""),
                "url": url,
                "published": datetime.fromtimestamp(it.get("time", 0), timezone.utc),
                "summary": "Hacker News front page story, %s points, %s comments." % (it.get("score", 0), it.get("descendants", 0)),
            })
    return out


def load_source(src):
    """Return (src, items, error)."""
    try:
        if src.get("type") == "hn":
            items = fetch_hn(src)
        else:
            items = parse_feed(fetch(src["url"]))
        if not items:
            return src, [], "no parseable items"
        return src, items, None
    except Exception as e:  # one feed must never fail the run
        return src, [], "%s: %s" % (type(e).__name__, e)


# ---------- dedupe ----------
STOP = set("a an the of to in on for and or with by at from as is are was were be its it this that new says say".split())


def norm_title(t):
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return " ".join(w for w in t.split() if w not in STOP)


def norm_url(u):
    p = urlsplit(u)
    q = [(k, v) for k, v in parse_qsl(p.query) if not k.startswith("utm_") and k not in ("ref", "source")]
    return urlunsplit((p.scheme, p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), urlencode(q), ""))


def similar(a, b):
    if a["nurl"] == b["nurl"]:
        return True
    ta, tb = a["ntitle"], b["ntitle"]
    if not ta or not tb:
        return False
    ratio = difflib.SequenceMatcher(None, ta, tb).ratio()
    sa, sb = set(ta.split()), set(tb.split())
    jac = len(sa & sb) / max(1, len(sa | sb))
    return ratio >= 0.72 or jac >= 0.6


def refine_topic(item):
    # Only refine for general-interest hints; specialised sources keep their hint.
    hint = item["topic"]
    text = item["title"] + " " + item["summary"][:200]
    if hint in ("Tech", "Dev", "AI", "Security"):
        for topic, pat in TOPIC_KEYWORDS:
            if topic in ("AI",) and hint != "Tech":
                continue
            if re.search(pat, item["title"], re.I if topic != "AI" else 0):
                if topic == "Breach" and hint in ("Tech", "Security"):
                    return "Breach"
                if topic == "Security" and hint in ("Tech", "Dev"):
                    return "Security"
                if topic == "Outage" and hint in ("Tech", "Dev"):
                    return "Outage"
                if topic == "AI" and hint == "Tech":
                    return "AI"
    return hint


def dedupe(raw):
    """raw: list of dicts with weight. Returns clustered items with coverage."""
    raw.sort(key=lambda x: (-x["weight"], x["published"]))
    clusters = []
    for it in raw:
        for c in clusters:
            if similar(c["rep"], it):
                c["members"].append(it)
                break
        else:
            clusters.append({"rep": it, "members": [it]})
    out = []
    for c in clusters:
        rep = c["rep"]
        sources = sorted({m["source"] for m in c["members"]})
        # prefer the longest summary among members
        summary = max((m["summary"] for m in c["members"]), key=len)
        out.append({
            "title": rep["title"],
            "url": rep["url"],
            "source": rep["source"],
            "published": max(m["published"] for m in c["members"]).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "summary": summary[:MAX_SUMMARY],
            "topic": rep["topic"],
            "coverage": len(sources),
            "also_covered_by": [s for s in sources if s != rep["source"]],
            "_w": sum(m["weight"] for m in c["members"]),
        })
    out.sort(key=lambda x: (-x["coverage"], -x["_w"], x["published"]), reverse=False)
    for o in out:
        del o["_w"]
        o["id"] = hashlib.sha1(norm_url(o["url"]).encode()).hexdigest()[:10]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--hours", type=float, default=30)
    ap.add_argument("--config", default=str(ROOT / "config" / "feeds.json"))
    ap.add_argument("--check", action="store_true", help="only report per-feed status")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text())
    chan = json.loads((ROOT / "config" / "channel.json").read_text())
    tz = ZoneInfo(chan.get("timezone", "UTC"))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=args.hours)
    cap = int(cfg.get("max_items_per_source", 40))

    t0 = time.time()
    raw, ok, bad = [], [], []
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        for src, items, err in ex.map(load_source, cfg["sources"]):
            if err:
                bad.append((src["name"], err))
                log("FAIL  %-26s %s" % (src["name"], err))
                continue
            recent = [i for i in items if i["published"] and i["published"] >= cutoff and i["published"] <= now + timedelta(hours=1)]
            recent.sort(key=lambda i: i["published"], reverse=True)
            ok.append(src["name"])
            log("ok    %-26s %3d parsed, %3d recent" % (src["name"], len(items), len(recent)))
            for i in recent[:cap]:
                i.update(source=src["name"], topic=src.get("topic", "Tech"), weight=float(src.get("weight", 1.0)),
                         ntitle=norm_title(i["title"]), nurl=norm_url(i["url"]))
                i["topic"] = refine_topic(i)
                raw.append(i)
    log("%d/%d sources ok in %.1fs" % (len(ok), len(cfg["sources"]), time.time() - t0))
    if args.check:
        return 0

    items = dedupe(raw)
    # Order: coverage desc, then newest
    items.sort(key=lambda x: (-x["coverage"], x["published"]), reverse=False)
    items.sort(key=lambda x: (x["coverage"], x["published"]), reverse=True)
    out = {
        "date": now.astimezone(tz).strftime("%Y-%m-%d"),
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_hours": args.hours,
        "sources_ok": ok,
        "sources_failed": [n for n, _ in bad],
        "items": [{k: v for k, v in i.items() if k in
                   ("id", "title", "url", "source", "published", "summary", "topic", "coverage", "also_covered_by")}
                  for i in items],
    }
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "news.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    log("wrote %d items (from %d raw) to %s" % (len(items), len(raw), outdir / "news.json"))
    if not items:
        log("WARNING: no items collected")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
