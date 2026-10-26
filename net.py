"""One way to make HTTPS requests, used by everything except the Claude SDK.

Uses the certificate bundle shipped inside the app (certifi), because a packaged
Mac app can't rely on the system's certificate settings. Only the Python
standard library otherwise, so there is nothing extra to break when packaged.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional

try:
    import certifi
    _CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:  # certifi missing: fall back to the system's settings
    _CONTEXT = ssl.create_default_context()


class HTTPError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status, self.body = status, body


def request(method: str, url: str, params: Optional[Dict] = None, form: Optional[Dict] = None,
            token: str = "", timeout: float = 15) -> Dict:
    """Send a request and return the JSON reply ({} if the reply is empty)."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CONTEXT) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise HTTPError(e.code, e.read().decode("utf-8", "replace")) from None
    return json.loads(body) if body.strip() else {}


def get(url: str, **kw) -> Dict:
    return request("GET", url, **kw)


def post(url: str, **kw) -> Dict:
    return request("POST", url, **kw)


def check() -> Dict[str, bool]:
    """Can this computer (or packaged app) reach the services it needs? Used by the self-test."""
    results = {}
    for name, url in (("google", "https://www.googleapis.com/oauth2/v3/certs"),
                      ("europepmc", "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=peroxisome&format=json&pageSize=1"),
                      ("ncbi", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi?retmode=json")):
        try:
            get(url, timeout=20)
            results[name] = True
        except Exception:
            results[name] = False
    return results
