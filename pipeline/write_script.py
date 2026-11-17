#!/usr/bin/env python3
"""Turn <outdir>/news.json into <outdir>/brief.json using Claude.

Two stages: (1) pick the most important stories from headlines, (2) fetch the
chosen articles and write the spoken script. Standard library only.

Claude access: ANTHROPIC_API_KEY (Messages API, claude-haiku-5-5) if set,
otherwise the Claude Code CLI in headless mode (`claude -p`, model sonnet;
authenticates via CLAUDE_CODE_OAUTH_TOKEN in CI).

    python pipeline/write_script.py --outdir out/2026-11-15
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date as date_cls
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect  # noqa: E402  (shared SSL context / user agent)

ROOT = Path(__file__).resolve().parent.parent
API_MODEL = "claude-haiku-5-5"
CLI_MODEL = "sonnet"
TAGS = ["AI", "Security", "Outage", "Breach", "Dev", "Tech"]
MAX_RETRIES = 2
ARTICLE_CHARS = 4000
CANDIDATES = 120


def log(msg):
    print(msg, file=sys.stderr, flush=True)


class Fail(Exception):
    pass


# ---------------------------------------------------------------- Claude ----
def call_api(system, prompt):
    body = json.dumps({
        "model": API_MODEL,
        "max_tokens": 4096,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "content-type": "application/json",
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01",
    })
    with urllib.request.urlopen(req, timeout=180, context=collect.SSL_CTX) as r:
        data = json.loads(r.read())
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def call_cli(system, prompt):
    cmd = ["claude", "-p", "--model", CLI_MODEL, "--tools", "", "--output-format", "json",
           "--no-session-persistence", "--disable-slash-commands", "--system-prompt", system]
    # Run from an empty temp dir so no project CLAUDE.md / settings leak into the prompt.
    with tempfile.TemporaryDirectory() as tmp:
        # An empty ANTHROPIC_API_KEY (as CI passes for an unset secret) must not reach the CLI,
        # or it tries key auth with a blank key instead of the subscription token.
        env = {k: v for k, v in os.environ.items() if not (k.startswith("ANTHROPIC_") and not v)}
        # A token pasted from a wrapped terminal line can carry a stray space or line break.
        if env.get("CLAUDE_CODE_OAUTH_TOKEN"):
            env["CLAUDE_CODE_OAUTH_TOKEN"] = "".join(env["CLAUDE_CODE_OAUTH_TOKEN"].split())
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=420, cwd=tmp, env=env)
    if p.returncode != 0:
        detail = (p.stderr or "").strip()[:300]
        try:  # the useful message is in the JSON's "result"/"subtype", not at the start of stdout
            res = json.loads(p.stdout)
            detail = "%s | %s | %s" % (res.get("subtype"), str(res.get("result"))[:400], detail)
        except ValueError:
            detail = "%s | %s" % (p.stdout[-400:], detail)
        raise Fail("claude CLI failed (%d): %s" % (p.returncode, detail))
    try:
        res = json.loads(p.stdout)
    except ValueError:
        raise Fail("claude CLI returned non-JSON: %s" % p.stdout[:300])
    if res.get("is_error"):
        raise Fail("claude CLI error: %s" % str(res.get("result"))[:500])
    return res.get("result", "")


def ask(system, prompt):
    fn = call_api if os.environ.get("ANTHROPIC_API_KEY") else call_cli
    for attempt in (1, 2):
        try:
            return fn(system, prompt)
        except Fail:
            if attempt == 2:
                raise
            time.sleep(3)
        except Exception as e:  # network / timeout
            if attempt == 2:
                raise Fail("%s: %s" % (type(e).__name__, e))
            time.sleep(3)


def extract_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < i:
        raise ValueError("no JSON object found in reply")
    return json.loads(t[i:j + 1])


def ask_json(system, prompt, validate):
    """Ask, parse, validate; on problems feed the errors back (up to MAX_RETRIES)."""
    errors, last = [], ""
    for attempt in range(MAX_RETRIES + 1):
        p = prompt
        if errors:
            p += ("\n\nYour previous reply was rejected:\n- " + "\n- ".join(errors) +
                  "\n\nPrevious reply:\n" + last + "\n\nReturn a corrected JSON object only.")
        last = ask(system, p)
        try:
            obj = extract_json(last)
            errors = validate(obj)
        except ValueError as e:
            obj, errors = None, ["reply was not valid JSON: %s" % e]
        if not errors:
            return obj
        log("attempt %d rejected: %s" % (attempt + 1, "; ".join(errors)))
    raise Fail("output still invalid after %d retries: %s" % (MAX_RETRIES, "; ".join(errors)))


# ----------------------------------------------------------- article text ----
class TextExtractor(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "aside", "header", "form", "noscript", "svg", "figure", "button"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.in_p = False
        self.in_article = 0
        self.cur = []
        self.paras = []        # all paragraphs
        self.art_paras = []    # paragraphs inside <article>/<main>

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in ("article", "main"):
            self.in_article += 1
        elif tag in ("p", "li", "h2", "h3") and not self.skip:
            self.in_p = True
            self.cur = []

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in ("article", "main"):
            self.in_article = max(0, self.in_article - 1)
        elif tag in ("p", "li", "h2", "h3") and self.in_p:
            self.in_p = False
            t = re.sub(r"\s+", " ", "".join(self.cur)).strip()
            if len(t) >= 40:
                self.paras.append(t)
                if self.in_article:
                    self.art_paras.append(t)

    def handle_data(self, data):
        if self.in_p and not self.skip:
            self.cur.append(data)


def fetch_article(url):
    """Best-effort article text; returns '' on any failure."""
    if "news.ycombinator.com" in url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": collect.UA, "Accept": "text/html,*/*;q=0.5"})
        with urllib.request.urlopen(req, timeout=15, context=collect.SSL_CTX) as r:
            ctype = r.headers.get("Content-Type", "")
            if "html" not in ctype and "xml" not in ctype:
                return ""
            raw = r.read(1_500_000)
            enc = r.headers.get_content_charset() or "utf-8"
        p = TextExtractor()
        p.feed(raw.decode(enc, errors="replace"))
        paras = p.art_paras if sum(map(len, p.art_paras)) > 400 else p.paras
        text, seen = [], set()
        for t in paras:
            if t not in seen:
                seen.add(t)
                text.append(t)
        return " ".join(text)[:ARTICLE_CHARS]
    except Exception as e:
        log("  article fetch failed (%s): %s" % (url[:70], type(e).__name__))
        return ""


# --------------------------------------------------------------- prompts ----
PICK_SYSTEM = ("You are the news editor of a daily spoken news brief for working IT professionals. "
               "You reply with a single JSON object and nothing else.")

SCRIPT_SYSTEM = ("You are the scriptwriter of a daily spoken news brief for working IT professionals. "
                 "You write for the ear, you are scrupulously faithful to the supplied material, and you "
                 "reply with a single JSON object and nothing else.")


def recent_coverage(today, days=4):
    """Headlines and source URLs from the last few published episodes (episodes/*.json)."""
    heads, urls = [], set()
    for f in sorted((ROOT / "episodes").glob("*.json"))[-days:]:
        if f.stem >= today:
            continue
        try:
            ep = json.loads(f.read_text())
        except ValueError:
            continue
        for seg in ep.get("segments", []):
            if seg.get("kind") == "story":
                heads.append("%s: %s" % (f.stem, seg.get("headline", "")))
                urls.update(src.get("url", "") for src in seg.get("sources", []))
    return heads, urls


def written(text):
    """Undo spoken spellings ("A I", "F B I") in text that is shown on screen, not read aloud."""
    return re.sub(r"\b(?:[A-Z] )+[A-Z]\b", lambda m: m.group(0).replace(" ", ""), text).strip()


def pick_prompt(items, n, covered=()):
    lines = []
    for i in items:
        lines.append("[%s] (%s | %s | covered by %d source%s) %s - %s" % (
            i["id"], i["topic"], i["source"], i["coverage"], "" if i["coverage"] == 1 else "s",
            i["title"], i["summary"][:220]))
    return """Choose the %(n)d most important stories for today's brief from the candidate list below.

Audience: a working IT professional or developer (many of them in India) who wants the day's must-know list: things they may need to patch, check, change or be able to explain at work today.

What to prefer, most important first:
1. Security they must act on: vulnerabilities being exploited now, major vendor patch releases (Microsoft, Google, Apple, Cisco, VMware, Linux and similar), certificate or trust changes, supply-chain compromises, end-of-life runtimes (PHP, Node and similar)
2. Breaches and incidents with concrete facts: who was hit, how it happened, what was exposed, what it cost
3. AI: model and product launches, pricing and availability changes from the major labs and tools, plus notable AI security research
4. Outages at major cloud and SaaS providers, and their published causes
5. Developer platform and tooling changes that alter how people work
6. India: CERT-In advisories, data protection (DPDP) and IT rules, incidents at Indian companies or affecting Indian users
7. Major industry news, only when it directly affects IT professionals (a regulator acting on an AI incident, a company filing that changes a product's future)

Rules:
- Each pick must pass this test: would a working engineer do something or decide something differently because of it? If not, skip it.
- De-prioritise executive departures and personal scandals, lawsuits and politics without a direct practical effect, funding rounds, opinion pieces, listicles, rumours and consumer gadget news. Pick one of these only if it is the single biggest technology story of the day.
- Prefer the primary source (the vendor's own advisory, blog or incident report) when it is among the candidates; otherwise the most detailed report.
- Aim for a mix: normally at least two security or breach items and at least one AI item when good ones exist. Include one India item when a concrete, verifiable one exists; never force one.
- Do not pick two items about the same event. If one event is covered by several outlets, that signals importance, but pick it once.
- Ignore items that are not about technology or IT and routine minor status-page entries with no real user impact.
- If several candidates report the same event or closely related facets of it (for example different outlets covering one incident), pick the best one as "id" and list the others under "related" so the writer can combine them.
- If fewer than %(n)d candidates are genuinely worth a listener's time, choose fewer. Never pad.
- Order the picks from most to least important.
%(covered)s
Candidates:
%(cands)s

Reply as JSON: {"picks": [{"id": "<candidate id>", "related": ["<ids of candidates about the same event>"], "why": "<few words>"}]}""" % {
        "n": n, "cands": "\n".join(lines),
        "covered": ("- Listeners heard the stories below in recent episodes. Do NOT pick them again. The only exception is a "
                    "genuinely new development since then (not a recap or more reaction); such a pick must be about the new development.\n"
                    "Already covered:\n" + "\n".join("  * " + h for h in covered) + "\n") if covered else ""}


def script_prompt(picks, n, target, date_phrase, channel, spoken_channel, per_story, total_words):
    blocks = []
    for p in picks:
        body = p["article"] or "(article text unavailable; only the feed summary below is known)"
        b = "=== STORY id=%s | topic hint: %s | source: %s | covered by %d source(s)\nTitle: %s\nFeed summary: %s\nArticle text: %s" % (
            p["id"], p["topic"], p["source"], p["coverage"], p["title"], p["summary"], body)
        for r in p["related"]:
            b += "\n  -- related report id=%s from %s: %s. %s" % (r["id"], r["source"], r["title"], r["summary"][:400])
        blocks.append(b)
    return """Write today's episode of "%(channel)s" from the %(k)d stories below.

HARD RULES
- Use ONLY facts present in the supplied feed summaries and article text. Never invent numbers, names, dates, versions, quotes or consequences. If details are thin, say less and keep that story short.
- Attribute with "according to <outlet>" wherever a claim rests on a single source, and for anything unconfirmed.
- Neutral, factual tone. No hype, no opinion, no advice beyond what the source itself states. A short "what to check" pointer is fine only if the source supports it.
- Written for the ear: short sentences, plain words, no URLs, no markdown, no bullet lists, no emoji, no parentheses.
- Easy on the ear: at most two or three figures per story, and round large ones the way a newsreader would ("about one hundred sixty thousand", "nearly a quarter") unless the exact value is the news (a version number, a price, a deadline). Never read out a string of precise counts.
- The spoken spellings below apply ONLY to the "intro", "outro" and story "text" fields. "title", "description", "tags" and "headline" are read on screen, so write those normally (AI, FBI, AWS, US-EAST-1, digits for numbers).
- Text-to-speech friendly: write numbers and money in words ("two hundred million dollars", "forty percent"); write dates spoken ("the twelfth of November"); write version numbers the way you would say them ("PHP seven point four", "Node twenty two"); write initialisms that are spoken letter by letter with spaces ("A I", "A P I", "C V E", "S Q L", "I T", "D N S"), and spell out symbols or unusual names phonetically only when the normal spelling would be mispronounced. Keep ordinary words and acronyms read as words (NASA, SaaS) as they are. Say CVE numbers digit by digit only if essential; otherwise leave them out.
- Total spoken length about %(total)d words across intro, stories and outro (target %(target)d for a full %(n)d-story episode). Each story roughly %(per)d words. Fewer stories means a shorter episode: do not pad.
- Keep the story order given. Each story must be a distinct event.

OUTPUT: one JSON object:
{
 "title": "YouTube title, at most 72 characters, naming the 2 or 3 biggest stories, plain and factual, no clickbait, no all caps, no emoji",
 "description": "2 or 3 plain-text sentences summarising the episode",
 "tags": ["8 to 15 short lowercase-or-proper tags relevant to the stories"],
 "thumbnail": "3 to 5 words, at most 30 characters, the single biggest story as a punchy factual label for the video thumbnail, normal writing, no full stop",
 "intro": "two sentences, about 30 words. FIRST sentence: no greeting; go straight into the top two or three stories as a fast factual teaser, biggest first, so the opening seconds give a reason to keep listening. SECOND sentence: name the show as '%(spoken)s' and give the day and date spoken naturally: %(date)s",
 "stories": [
   {"id": "<the story id above>", "headline": "at most 60 characters, plain on-screen headline, normal writing (not phonetic)", "tag": "one of AI, Security, Outage, Breach, Dev, Tech", "text": "the spoken text for this story", "source_ids": ["<ids of the story and of any related reports this text draws on>"]}
 ],
 "outro": "short sign-off, one or two sentences, no calls to action beyond thanking the listener"
}

The first sentence of each story's text should state the news plainly. Start later stories with a light natural transition only if it fits; do not announce story numbers.

STORIES
%(blocks)s""" % {"channel": channel, "k": len(picks), "total": total_words, "target": target, "n": n,
                 "per": per_story, "spoken": spoken_channel, "date": date_phrase,
                 "blocks": "\n\n".join(blocks)}


# ------------------------------------------------------------ validation ----
ORD = ["zeroth", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
       "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth",
       "nineteenth", "twentieth", "twenty first", "twenty second", "twenty third", "twenty fourth",
       "twenty fifth", "twenty sixth", "twenty seventh", "twenty eighth", "twenty ninth", "thirtieth",
       "thirty first"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]")


def date_phrase(d):
    dt = date_cls.fromisoformat(d)
    return "%s, the %s of %s" % (dt.strftime("%A"), ORD[dt.day], MONTHS[dt.month - 1])


def wc(s):
    return len(re.findall(r"\b[\w'.-]+\b", s))


def spoken_ok(label, text, errs):
    if not isinstance(text, str) or not text.strip():
        errs.append("%s is empty" % label)
        return
    if re.search(r"https?://|www\.|\.com\b", text):
        errs.append("%s contains a URL" % label)
    if re.search(r"[*#_`<>\[\]{}|]|^\s*[-•]", text):
        errs.append("%s contains markdown or symbols" % label)
    if EMOJI.search(text):
        errs.append("%s contains emoji" % label)
    if re.search(r"[$%€£]", text):
        errs.append("%s contains a symbol (write money/percent as words)" % label)


def validate_picks(obj, by_id, n):
    errs = []
    picks = obj.get("picks")
    if not isinstance(picks, list) or not picks:
        return ["'picks' must be a non-empty list"]
    ids = []
    for p in picks:
        pid = p.get("id") if isinstance(p, dict) else None
        if pid not in by_id:
            errs.append("unknown id %r" % pid)
        ids.append(pid)
        rel = p.get("related", []) if isinstance(p, dict) else []
        if not isinstance(rel, list) or any(r not in by_id for r in rel):
            errs.append("'related' must be a list of candidate ids")
    if len(set(ids)) != len(ids):
        errs.append("duplicate ids in picks")
    if len(ids) > n:
        errs.append("at most %d picks allowed, got %d" % (n, len(ids)))
    return errs


def validate_script(obj, packs, n, target, picks_count):
    errs = []
    if not isinstance(obj, dict):
        return ["reply must be a JSON object"]
    title = obj.get("title", "")
    if not isinstance(title, str) or not title.strip() or len(title) > 72:
        errs.append("title must be 1-72 chars (got %d)" % len(title or ""))
    if EMOJI.search(str(title)):
        errs.append("title contains emoji")
    desc = obj.get("description", "")
    if not isinstance(desc, str) or len(desc.strip()) < 40 or len(desc) > 600:
        errs.append("description must be 2-3 plain sentences (40-600 chars)")
    tags = obj.get("tags")
    if not isinstance(tags, list) or not (8 <= len(tags) <= 15) or not all(isinstance(t, str) and 0 < len(t) <= 30 for t in tags):
        errs.append("tags must be 8-15 short strings (<= 30 chars)")
    spoken_ok("intro", obj.get("intro"), errs)
    spoken_ok("outro", obj.get("outro"), errs)
    stories = obj.get("stories")
    if not isinstance(stories, list) or not stories:
        return errs + ["'stories' must be a non-empty list"]
    if len(stories) != picks_count:
        errs.append("expected exactly %d stories (one per supplied story), got %d" % (picks_count, len(stories)))
    total = wc(obj.get("intro") or "") + wc(obj.get("outro") or "")
    seen = set()
    for k, s in enumerate(stories, 1):
        lab = "story %d" % k
        if not isinstance(s, dict):
            errs.append("%s is not an object" % lab)
            continue
        h = s.get("headline", "")
        if not isinstance(h, str) or not h.strip() or len(h) > 60:
            errs.append("%s headline must be 1-60 chars (got %d)" % (lab, len(h or "")))
        if s.get("tag") not in TAGS:
            errs.append("%s tag must be one of %s" % (lab, "|".join(TAGS)))
        spoken_ok(lab + " text", s.get("text"), errs)
        total += wc(s.get("text") or "")
        sids = s.get("source_ids")
        allowed = packs.get(s.get("id"))
        if allowed is None:
            errs.append("%s id must be one of the supplied story ids" % lab)
        elif not isinstance(sids, list) or not sids or not set(sids) <= allowed or s.get("id") not in sids:
            errs.append("%s source_ids must include its own story id and only ids supplied with that story" % lab)
        if s.get("id") in seen:
            errs.append("%s duplicates an earlier story" % lab)
        seen.add(s.get("id"))
    # word window scales with the number of stories actually written
    expected = 70 + (target - 70) * len(stories) / float(n)
    lo, hi = int(expected * 0.8), int(expected * 1.2)
    if not lo <= total <= hi:
        errs.append("total spoken words is %d, must be between %d and %d (aim for about %d)" % (total, lo, hi, int(expected)))
    obj["_words"] = total
    return errs


# ------------------------------------------------------------------ main ----
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()
    outdir = Path(args.outdir)

    chan = json.loads((ROOT / "config" / "channel.json").read_text())
    n = int(chan["stories_per_episode"])
    target = int(chan["target_words"])
    news = json.loads((outdir / "news.json").read_text())
    items = news.get("items", [])
    if not items:
        raise Fail("news.json has no items; nothing to write")
    by_id = {i["id"]: i for i in items}
    dstr = news["date"]
    dphrase = date_phrase(dstr)
    spoken_channel = re.sub(r"\bAI\b", "A I", chan["channel_name"]).replace(" IT ", " I T ").replace("&", "and")
    spoken_channel = re.sub(r"\band\b", "and", spoken_channel)
    mode = "Messages API (%s)" % API_MODEL if os.environ.get("ANTHROPIC_API_KEY") else "claude CLI (%s)" % CLI_MODEL
    log("Claude access: %s; %d candidate items" % (mode, len(items)))

    # Stage 1: choose stories from headlines.
    t0 = time.time()
    covered, used_urls = recent_coverage(dstr)
    fresh = [i for i in items if i["url"] not in used_urls]
    log("%d recent headlines to avoid; %d of %d items are articles already used" % (len(covered), len(items) - len(fresh), len(items)))
    cands = fresh[:CANDIDATES]  # news.json is already ordered by coverage then recency
    cmap = {i["id"]: i for i in cands}
    picks_obj = ask_json(PICK_SYSTEM, pick_prompt(cands, n, covered), lambda o: validate_picks(o, cmap, n))
    picked = [by_id[p["id"]] for p in picks_obj["picks"]]
    related = {p["id"]: [by_id[r] for r in p.get("related", []) if r != p["id"] and r in by_id]
               for p in picks_obj["picks"]}
    log("stage 1 (%.0fs) picked:" % (time.time() - t0))
    for p in picked:
        log("  - [%s] %s (%s)" % (p["topic"], p["title"], p["source"]))

    # Fetch article text for the chosen stories (best effort).
    t1 = time.time()
    with cf.ThreadPoolExecutor(max_workers=5) as ex:
        texts = list(ex.map(lambda p: fetch_article(p["url"]), picked))
    packs = []
    for p, t in zip(picked, texts):
        packs.append(dict(p, article=t, related=related[p["id"]]))
        log("  article text: %5d chars  %s" % (len(t), p["source"]))
    log("fetched articles in %.0fs" % (time.time() - t1))

    # Stage 2: write the script.
    t2 = time.time()
    k = len(packs)
    total_words = int(70 + (target - 70) * k / float(n))
    per_story = int((total_words - 70) / k)
    prompt = script_prompt(packs, n, target, dphrase, chan["channel_name"], spoken_channel, per_story, total_words)
    pack_ids = {p["id"]: {p["id"]} | {r["id"] for r in p["related"]} for p in packs}
    obj = ask_json(SCRIPT_SYSTEM, prompt, lambda o: validate_script(o, pack_ids, n, target, k))
    log("stage 2 done in %.0fs" % (time.time() - t2))

    segments = [{"kind": "intro", "text": obj["intro"].strip()}]
    for s in obj["stories"]:
        srcs, seen = [], set()
        for sid in s["source_ids"]:
            it = by_id[sid]
            if it["url"] not in seen:
                seen.add(it["url"])
                srcs.append({"name": it["source"], "url": it["url"]})
        segments.append({"kind": "story", "headline": written(s["headline"]), "tag": s["tag"],
                         "text": s["text"].strip(), "sources": srcs})
    segments.append({"kind": "outro", "text": obj["outro"].strip()})
    # Optional: fall back to the lead headline if the thumbnail label is missing or too long.
    thumb = written(str(obj.get("thumbnail") or "")).rstrip(".")
    if not (0 < len(thumb) <= 34):
        thumb = ""
    brief = {
        "channel": chan["channel_name"],
        "date": dstr,
        "title": written(obj["title"]),
        "description": written(obj["description"]),
        "tags": [written(t) for t in obj["tags"]],
        "thumbnail_text": thumb,
        "segments": segments,
    }
    (outdir / "brief.json").write_text(json.dumps(brief, indent=2, ensure_ascii=False))
    log("wrote %s  (%d stories, %d words)" % (outdir / "brief.json", k, obj["_words"]))


if __name__ == "__main__":
    try:
        main()
    except Fail as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print("ERROR: missing file: %s" % e, file=sys.stderr)
        sys.exit(1)
