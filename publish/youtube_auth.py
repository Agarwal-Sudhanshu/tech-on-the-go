#!/usr/bin/env python3
"""One-time YouTube OAuth helper (standard library only; runs on Python 3.8+).

    python3 publish/youtube_auth.py --client-secrets ~/Downloads/client_secret_XXX.json
                                    [--playlist] [--set-github-secrets] [--repo OWNER/REPO]

Opens your browser, you approve access, and the refresh token is saved to
.secrets/youtube.json (mode 600). Secrets and tokens are never printed.

Scopes: youtube.upload (upload) + youtube.readonly (to show the channel name).
--playlist requests the broader `youtube` scope instead of readonly, which the API
requires for playlistItems.insert (youtube.upload alone is not accepted there).
"""
import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path


def _ssl_context():
    """python.org builds of Python on macOS ship without CA certificates; use the system bundle."""
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs():
        for bundle in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
            if os.path.exists(bundle):
                ctx.load_verify_locations(bundle)
                break
    return ctx


SSL_CTX = _ssl_context()

ROOT = Path(__file__).resolve().parent.parent
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true"
SCOPE_UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
SCOPE_READONLY = "https://www.googleapis.com/auth/youtube.readonly"
SCOPE_FULL = "https://www.googleapis.com/auth/youtube"


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_client(path):
    try:
        data = json.loads(Path(path).expanduser().read_text())
    except (OSError, ValueError) as e:
        die(f"cannot read client secrets file: {e}")
    c = data.get("installed") or data.get("web")
    if not c or "client_id" not in c or "client_secret" not in c:
        die("that file is not an OAuth client JSON (expected an 'installed' section). "
            "Create the client as type 'Desktop app'.")
    return c["client_id"], c["client_secret"]


def post_form(url, fields):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read())
            msg = f"{d.get('error')}: {d.get('error_description', '')}"
        except Exception:
            msg = f"HTTP {e.code}"
        die(f"token exchange failed ({msg})")
    except OSError as e:
        die(f"network error: {e}")


def wait_for_code(state, port_holder, ready):
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" not in q and "error" not in q:
                self.send_response(404)
                self.end_headers()
                return
            result["q"] = {k: v[0] for k, v in q.items()}
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h3>Done. You can close this tab and return to the terminal.</h3>")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port_holder.append(srv.server_address[1])
    ready.set()
    srv.timeout = 5
    deadline = time.time() + 300
    while "q" not in result and time.time() < deadline:
        srv.handle_request()
    srv.server_close()
    return result.get("q")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-secrets", required=True, help="path to the downloaded client_secret.json")
    ap.add_argument("--playlist", action="store_true", help="also allow adding videos to a playlist")
    ap.add_argument("--set-github-secrets", action="store_true",
                    help="store YT_CLIENT_ID/YT_CLIENT_SECRET/YT_REFRESH_TOKEN via the gh CLI")
    ap.add_argument("--repo", help="OWNER/REPO for gh (default: repo of the current folder)")
    args = ap.parse_args()

    client_id, client_secret = load_client(args.client_secrets)
    scopes = [SCOPE_UPLOAD, SCOPE_FULL if args.playlist else SCOPE_READONLY]

    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)

    port_holder, ready = [], threading.Event()
    box = {}
    t = threading.Thread(target=lambda: box.update(q=wait_for_code(state, port_holder, ready)), daemon=True)
    t.start()
    ready.wait()
    redirect = f"http://127.0.0.1:{port_holder[0]}"

    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    })
    print("Opening your browser. Sign in with the Google account that owns the YouTube channel")
    print("and choose the right channel if asked. If the browser does not open, open this link:")
    print(url)
    webbrowser.open(url)
    t.join(timeout=310)

    q = box.get("q")
    if not q:
        die("timed out waiting for the browser sign-in")
    if q.get("error"):
        die(f"Google returned an error: {q['error']}")
    if q.get("state") != state:
        die("state mismatch; aborting")

    tok = post_form(TOKEN_URL, {
        "client_id": client_id, "client_secret": client_secret, "code": q["code"],
        "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect,
    })
    refresh = tok.get("refresh_token")
    if not refresh:
        die("Google did not return a refresh token. Revoke the app at "
            "https://myaccount.google.com/permissions and run this again.")

    # Which channel does this token belong to?
    req = urllib.request.Request(CHANNELS_URL, headers={"Authorization": f"Bearer {tok['access_token']}"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
            items = json.loads(r.read()).get("items", [])
        channel = items[0]["snippet"]["title"] if items else None
    except Exception as e:
        channel = None
        print(f"WARNING: could not look up channel name ({type(e).__name__}).", file=sys.stderr)

    d = ROOT / ".secrets"
    d.mkdir(mode=0o700, exist_ok=True)
    (d / ".gitignore").write_text("*\n")
    f = d / "youtube.json"
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"YT_CLIENT_ID": client_id, "YT_CLIENT_SECRET": client_secret,
                   "YT_REFRESH_TOKEN": refresh}, fh, indent=2)
    os.chmod(f, 0o600)

    if args.set_github_secrets:
        for name, value in (("YT_CLIENT_ID", client_id), ("YT_CLIENT_SECRET", client_secret),
                            ("YT_REFRESH_TOKEN", refresh)):
            cmd = ["gh", "secret", "set", name] + (["--repo", args.repo] if args.repo else [])
            try:
                p = subprocess.run(cmd, input=value.encode(), capture_output=True, cwd=ROOT)
            except FileNotFoundError:
                die("gh CLI not found")
            if p.returncode != 0:
                die(f"gh secret set {name} failed: {p.stderr.decode().strip()[:300]}")
        print("GitHub secrets set: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN")

    print(f"Success. Token saved to {f}")
    print(f"Authorized YouTube channel: {channel or '(unknown, lookup failed)'}")


if __name__ == "__main__":
    main()
