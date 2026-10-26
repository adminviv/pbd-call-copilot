"""Google Calendar: sign in (read-only), list meetings, and the confirmed-meeting rule.

Any Google account can sign in. The app asks only to READ calendars (no Gmail,
no changes): it sees the signed-in account's own calendar plus every calendar
shared with it, e.g. Andrew's shared calendar when Genevieve signs in.

The Copilot can only be switched on during a confirmed meeting:
  - the event is confirmed (not cancelled) and has a start and end time
  - at least 2 people are invited
  - the calendar's owner organised it or accepted it (not declined / no reply)
  - it has a video-call link (Zoom, Teams, Meet, Webex)
  - now is between 10 minutes before the start and 15 minutes after the end

Stored on this computer only: the sign-in token in ~/.pbd-copilot/google_token.json.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email",
          "https://www.googleapis.com/auth/calendar.readonly"]
HOME = Path.home() / ".pbd-copilot"
TOKEN = HOME / "google_token.json"
EARLY_MIN, LATE_MIN = 10, 15          # switch works 10 min before start to 15 min after end
LOOK_BACK_DAYS, LOOK_AHEAD_DAYS = 7, 7
VIDEO = re.compile(r"(zoom\.us/|zoomgov\.com/|teams\.microsoft\.com/|teams\.live\.com/|"
                   r"meet\.google\.com/|webex\.com/)", re.I)
API = "https://www.googleapis.com/calendar/v3"


# -- sign-in ---------------------------------------------------------------
# Talks to Google directly with Python's standard library (see net.py): the
# google-auth package drags in a compiled encryption library that broke in the
# packaged Mac app ("Symbol not found: _SSL_get0_group_name").
USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
REVOKE = "https://oauth2.googleapis.com/revoke"


def _net():
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import net
    return net


def client_config() -> Optional[dict]:
    """The app's Google sign-in client: bundled in the release build, or in the
    user folder while developing. None means calendar sign-in isn't set up."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for path in (root / "google_client.json", HOME / "google_client.json"):
        if path.exists():
            return json.loads(path.read_text())
    return None


def _client() -> dict:
    cfg = client_config()
    if not cfg:
        raise RuntimeError("Calendar sign-in isn't set up in this version of the app.")
    return cfg.get("installed") or cfg.get("web")


def _save(tok: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(json.dumps(tok))
    try:
        os.chmod(TOKEN, 0o600)
    except OSError:
        pass


def _expiry(tok: dict) -> dt.datetime:
    try:
        e = tok.get("expiry", "").replace("Z", "+00:00")
        t = dt.datetime.fromisoformat(e.split(".")[0] + ("+00:00" if "+" not in e else e[e.index("+"):]))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def _access_token() -> Optional[str]:
    """A valid access token, refreshed when needed; None if not signed in."""
    if not TOKEN.exists():
        return None
    tok = json.loads(TOKEN.read_text())
    if tok.get("token") and _expiry(tok) > dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60):
        return tok["token"]
    if not tok.get("refresh_token"):
        return None
    c = _client()
    net = _net()
    try:
        r = net.post(tok.get("token_uri") or c["token_uri"], form={
            "grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
            "client_id": tok.get("client_id") or c["client_id"],
            "client_secret": tok.get("client_secret") or c["client_secret"]})
    except net.HTTPError as e:
        if e.status in (400, 401):   # revoked or expired: sign in again
            return None
        raise
    tok["token"] = r["access_token"]
    tok["expiry"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=int(r.get("expires_in", 3600)))).isoformat()
    _save(tok)
    return tok["token"]


def sign_in(timeout_s: int = 300) -> str:
    """Opens Google's sign-in page in the browser; returns the signed-in email."""
    import base64
    import hashlib
    import secrets
    import urllib.parse
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer

    c = _client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got: Dict[str, str] = {}

    class Reply(BaseHTTPRequestHandler):
        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if q.get("state") == state:
                got.update(q)
            ok = "code" in got
            msg = ("Signed in. You can close this tab and go back to PBD Call Copilot." if ok else
                   "Sign-in was not completed. You can close this tab and try again in PBD Call Copilot.")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<html><body style='font-family:sans-serif;padding:40px'><h2>{msg}</h2></body></html>".encode())

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Reply)
    redirect = f"http://127.0.0.1:{server.server_port}/"
    url = c["auth_uri"] + "?" + urllib.parse.urlencode({
        "client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(SCOPES), "code_challenge": challenge, "code_challenge_method": "S256",
        "state": state, "access_type": "offline", "prompt": "consent select_account"})
    webbrowser.open(url)
    server.timeout = 1
    deadline = dt.datetime.now() + dt.timedelta(seconds=timeout_s)
    while not got and dt.datetime.now() < deadline:
        server.handle_request()
    server.server_close()
    if "code" not in got:
        raise RuntimeError("Sign-in was cancelled or timed out." if not got.get("error") else got["error"])
    r = _net().post(c["token_uri"], form={
        "grant_type": "authorization_code", "code": got["code"], "redirect_uri": redirect,
        "client_id": c["client_id"], "client_secret": c["client_secret"], "code_verifier": verifier})
    _save({"token": r["access_token"], "refresh_token": r.get("refresh_token", ""),
           "expiry": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=int(r.get("expires_in", 3600)))).isoformat(),
           "token_uri": c["token_uri"], "client_id": c["client_id"], "client_secret": c["client_secret"],
           "scopes": SCOPES})
    return signed_in_email() or ""


def sign_out() -> None:
    try:
        tok = json.loads(TOKEN.read_text())
        _net().post(REVOKE, form={"token": tok.get("refresh_token") or tok.get("token", "")})
    except Exception:
        pass
    TOKEN.unlink(missing_ok=True)


def signed_in_email() -> Optional[str]:
    token = _access_token()
    if not token:
        return None
    try:
        return _net().get(USERINFO, token=token).get("email")
    except Exception:
        return None


# -- meetings ------------------------------------------------------------------
def fetch_events(now: Optional[dt.datetime] = None) -> List[Dict]:
    """Timed events from every visible calendar, 7 days back to 7 days ahead,
    one entry per meeting even when it sits on several calendars."""
    token = _access_token()
    if not token:
        return []
    import urllib.parse
    net = _net()
    now = now or dt.datetime.now(dt.timezone.utc)
    lo = (now - dt.timedelta(days=LOOK_BACK_DAYS)).isoformat()
    hi = (now + dt.timedelta(days=LOOK_AHEAD_DAYS)).isoformat()
    cals = net.get(f"{API}/users/me/calendarList", params={"minAccessRole": "reader"}, token=token)
    merged: Dict[str, Dict] = {}
    for cal in cals.get("items", []):
        if cal.get("hidden") or cal.get("selected") is False:
            continue
        try:
            r = net.get(f"{API}/calendars/{urllib.parse.quote(cal['id'], safe='')}/events", token=token, params={
                "timeMin": lo, "timeMax": hi, "singleEvents": "true", "orderBy": "startTime", "maxResults": 250})
        except net.HTTPError:
            continue
        for ev in r.get("items", []):
            if "dateTime" not in ev.get("start", {}):
                continue   # all-day items aren't calls
            item = summarize(ev, cal["id"], cal.get("summary", ""))
            key = ev.get("iCalUID") or ev["id"]
            if key not in merged or (item["confirmed"] and not merged[key]["confirmed"]):
                merged[key] = item
    return sorted(merged.values(), key=lambda m: m["start"])


def is_confirmed(ev: Dict, calendar_id: str) -> Tuple[bool, str]:
    """The rule in the module docstring. Returns (confirmed, reason if not)."""
    if ev.get("status") != "confirmed":
        return False, "cancelled"
    people = [a for a in ev.get("attendees", []) if not a.get("resource")]
    if len(people) < 2:
        return False, "fewer than 2 people invited"
    owner = calendar_id.lower()
    organizer = ev.get("organizer", {})
    me = next((a for a in people if a.get("self") or a.get("email", "").lower() == owner), None)
    organised = organizer.get("self") or organizer.get("email", "").lower() == owner
    if not organised:
        status = (me or {}).get("responseStatus")
        if status != "accepted":
            return False, {"declined": "declined", "tentative": "only tentative"}.get(status, "not accepted yet")
    if not video_link(ev):
        return False, "no video-call link"
    return True, ""


def video_link(ev: Dict) -> str:
    if ev.get("hangoutLink"):
        return ev["hangoutLink"]
    for ep in ev.get("conferenceData", {}).get("entryPoints", []):
        if ep.get("entryPointType") == "video" and ep.get("uri"):
            return ep["uri"]
    m = VIDEO.search(" ".join([ev.get("location", ""), ev.get("description", "")]))
    return m.group(0) if m else ""


def summarize(ev: Dict, calendar_id: str, calendar_name: str) -> Dict:
    ok, why = is_confirmed(ev, calendar_id)
    people = [a for a in ev.get("attendees", []) if not a.get("resource")]
    return {
        "id": ev.get("id"), "title": ev.get("summary") or "(no title)",
        "start": ev["start"]["dateTime"], "end": ev["end"]["dateTime"],
        "calendar": calendar_name, "confirmed": ok, "why_not": why,
        "attendees": [a.get("displayName") or a.get("email", "").split("@")[0] for a in people],
        "description": re.sub(r"https?://\S+", "", ev.get("description", "") or "")[:800],
    }


def _t(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def active_meeting(meetings: List[Dict], now: Optional[dt.datetime] = None) -> Optional[Dict]:
    """The confirmed meeting the switch may be used for right now, if any."""
    now = now or dt.datetime.now(dt.timezone.utc)
    open_now = [m for m in meetings if m["confirmed"]
                and _t(m["start"]) - dt.timedelta(minutes=EARLY_MIN) <= now
                <= _t(m["end"]) + dt.timedelta(minutes=LATE_MIN)]
    return min(open_now, key=lambda m: abs((_t(m["start"]) - now).total_seconds()), default=None)


def meeting_context(m: Optional[Dict]) -> str:
    """What Claude is told about the meeting, so explanations fit the whole call."""
    if not m:
        return ""
    who = ", ".join(m["attendees"][:12])
    desc = " ".join(m["description"].split())
    return (f"Meeting: {m['title']}\nInvited: {who}\n" + (f"Agenda/notes: {desc}\n" if desc else ""))
