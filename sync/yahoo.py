"""Minimal Yahoo Fantasy Sports API client (stdlib only).

Auth: OAuth 2.0 with a long-lived refresh token. The client id and secret
come from environment variables (GitHub Actions secrets); the refresh token
is passed in by the caller.
"""
import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2/"
DEFAULT_REDIRECT = "https://localhost:8080"


def _basic(client_id, client_secret):
    raw = f"{client_id}:{client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _post_token(client_id, client_secret, fields):
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={
            "Authorization": _basic(client_id, client_secret),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise SystemExit(f"Yahoo token request failed ({e.code}): {detail}")


def exchange_code(client_id, client_secret, code, redirect_uri=DEFAULT_REDIRECT):
    return _post_token(client_id, client_secret, {
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code": code.strip(),
    })


def auth_url(client_id, redirect_uri=DEFAULT_REDIRECT):
    q = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "fspt-r",
    })
    return f"{AUTH_URL}?{q}"


class Yahoo:
    def __init__(self, client_id, client_secret, refresh_token,
                 redirect_uri=DEFAULT_REDIRECT):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.redirect_uri = redirect_uri
        self.access_token = None
        self.expires_at = 0
        self.calls = 0

    def _refresh(self):
        tok = _post_token(self.client_id, self.client_secret, {
            "grant_type": "refresh_token",
            "redirect_uri": self.redirect_uri,
            "refresh_token": self.refresh_token,
        })
        self.access_token = tok["access_token"]
        # Yahoo may rotate the refresh token; keep the newest one.
        self.refresh_token = tok.get("refresh_token", self.refresh_token)
        self.expires_at = time.time() + int(tok.get("expires_in", 3600)) - 120

    def get(self, path, retries=3):
        """GET a fantasy API path and return the normalized JSON."""
        if not self.access_token or time.time() > self.expires_at:
            self._refresh()
        sep = "&" if "?" in path else "?"
        url = API_BASE + path + sep + "format=json"
        for attempt in range(retries):
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {self.access_token}"})
            try:
                with urllib.request.urlopen(req, timeout=45) as r:
                    self.calls += 1
                    return normalize(json.load(r)).get("fantasy_content", {})
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt < retries - 1:
                    self._refresh()
                    continue
                if e.code in (429, 500, 502, 503, 999) and attempt < retries - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                detail = e.read().decode(errors="replace")[:300]
                raise RuntimeError(f"Yahoo API {e.code} on {path}: {detail}")
            except urllib.error.URLError:
                if attempt < retries - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        raise RuntimeError(f"Yahoo API gave up on {path}")


# ---------------------------------------------------------------------------
# Yahoo's JSON is awkward: collections are dicts keyed "0","1",... plus
# "count", and single objects are split into lists of one-key fragments.
# normalize() turns that into ordinary dicts and lists.

def _is_index(k):
    return isinstance(k, str) and k.isdigit()


def normalize(x):
    if isinstance(x, dict):
        idx = sorted((k for k in x if _is_index(k)), key=int)
        if idx and "count" in x and all(_is_index(k) or k == "count" for k in x):
            return [normalize(x[k]) for k in idx]
        out = {}
        for k, v in x.items():
            if _is_index(k):
                continue
            out[k] = normalize(v)
        # Numeric-keyed children without a count are extra properties.
        for k in idx:
            child = normalize(x[k])
            if isinstance(child, dict):
                for ck, cv in child.items():
                    out.setdefault(ck, cv)
            else:
                out.setdefault("_items", []).append(child)
        return out
    if isinstance(x, list):
        items = []
        for i in x:
            n = normalize(i)
            if isinstance(n, list) and all(isinstance(j, dict) for j in n) and \
                    _mergeable(n):
                n = _merge(n)
            if n == [] or n == {}:
                continue
            items.append(n)
        if items and all(isinstance(i, dict) for i in items) and _mergeable(items):
            return _merge(items)
        return items
    return x


def _mergeable(dicts):
    seen = set()
    for d in dicts:
        for k in d:
            if k in seen:
                return False
            seen.add(k)
    return len(dicts) > 1 or (len(dicts) == 1 and len(dicts[0]) > 0 and
                               not _single_entity(dicts[0]))


def _single_entity(d):
    """A lone {"manager": {...}} inside a list is a one-item collection."""
    return len(d) == 1 and isinstance(next(iter(d.values())), dict) and \
        next(iter(d)) in ("manager", "team", "player", "matchup", "transaction",
                          "league", "game", "draft_result")


def _merge(dicts):
    out = {}
    for d in dicts:
        out.update(d)
    return out


def as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def unwrap(items, key):
    """[{"team": {...}}, ...] -> [{...}, ...]"""
    out = []
    for i in as_list(items):
        if isinstance(i, dict) and key in i:
            out.append(i[key])
        elif isinstance(i, dict):
            out.append(i)
    return out
