"""Read-only Gmail access: OAuth for an installed app (loopback redirect) and a tiny REST client."""
import base64
import hashlib
import http.server
import json
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CLIENT = (config.ANTHROPIC_KEYCHAIN[0], "google-oauth-client")
REFRESH = (config.ANTHROPIC_KEYCHAIN[0], "google-refresh-token")
API = "https://gmail.googleapis.com/gmail/v1/users/me"


class GoogleAuthError(Exception):
    """No usable refresh token: run `parcel-finder authorise-google`."""


def _client_config() -> dict:
    return json.loads(config.secret(*CLIENT))["installed"]


def _post_form(url: str, fields: dict) -> dict:
    req = urllib.request.Request(url, urllib.parse.urlencode(fields).encode(),
                                 {"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            body = json.load(e)
        except ValueError:
            body = {}
        raise GoogleAuthError(f"token endpoint said {e.code}: {body.get('error', '')}") from None


def authorise() -> None:
    """One-off consent in the browser; stores the refresh token in the keychain."""
    c = _client_config()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state and "code" in q:
                got["code"] = q["code"][0]
                text = b"parcel-finder: Gmail authorised, you can close this tab."
            else:
                text = b"parcel-finder: authorisation failed (state mismatch or no code)."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(text)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{srv.server_address[1]}"
    url = c["auth_uri"] + "?" + urllib.parse.urlencode({
        "client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    t = threading.Thread(target=lambda: [srv.handle_request() for _ in range(3) if "code" not in got], daemon=True)
    t.start()
    subprocess.run(["open", url])
    t.join(300)
    srv.server_close()
    if "code" not in got:
        raise GoogleAuthError("no authorisation code received")
    r = _post_form(c["token_uri"], {"code": got["code"], "client_id": c["client_id"],
                                    "client_secret": c["client_secret"], "redirect_uri": redirect,
                                    "grant_type": "authorization_code", "code_verifier": verifier})
    if not r.get("refresh_token"):
        raise GoogleAuthError("no refresh token in the reply")
    config.store_secret(*REFRESH, r["refresh_token"])


class Gmail:
    def __init__(self):
        self._token, self._expires = None, 0.0

    def _access_token(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        try:
            refresh = config.secret(*REFRESH)
        except config.MissingSecret:
            raise GoogleAuthError("no refresh token: run `parcel-finder authorise-google`") from None
        c = _client_config()
        r = _post_form(c["token_uri"], {"refresh_token": refresh, "client_id": c["client_id"],
                                        "client_secret": c["client_secret"], "grant_type": "refresh_token"})
        self._token, self._expires = r["access_token"], time.time() + int(r.get("expires_in", 3600))
        return self._token

    def _get(self, path: str, params: list[tuple[str, str]]) -> dict:
        url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
        for attempt in range(6):
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._access_token()}"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                body = e.read()
                reasons = {x.get("reason") for x in json.loads(body or b"{}").get("error", {}).get("errors", [])} \
                    if body.startswith(b"{") else set()
                # Gmail's per-user units-per-minute quota: a big first fill trips it; back off and retry.
                rate_limited = e.code == 429 or (e.code == 403 and bool(
                    reasons & {"rateLimitExceeded", "userRateLimitExceeded"}))
                if rate_limited and attempt < 5:
                    time.sleep(min(60, 5 * 2 ** attempt))
                    continue
                raise urllib.error.HTTPError(e.url, e.code, f"{e.reason} {sorted(map(str, reasons))}", e.headers,
                                             None) from None

    def search(self, query: str) -> list[str]:
        ids, page = [], None
        while True:
            params = [("q", query), ("maxResults", "500")] + ([("pageToken", page)] if page else [])
            r = self._get("messages", params)
            ids += [m["id"] for m in r.get("messages", [])]
            page = r.get("nextPageToken")
            if not page:
                return ids

    def message(self, mid: str, full: bool = False) -> dict:
        if full:
            return self._get(f"messages/{mid}", [("format", "full")])
        return self._get(f"messages/{mid}", [("format", "metadata"), ("metadataHeaders", "From"),
                                             ("metadataHeaders", "Subject")])
