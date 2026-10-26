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


# -- sign-in -----------------------------------------------------------------
def client_config() -> Optional[dict]:
    """The app's Google sign-in client: bundled in the release build, or in the
    user folder while developing. None means calendar sign-in isn't set up."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for path in (root / "google_client.json", HOME / "google_client.json"):
        if path.exists():
            return json.loads(path.read_text())
    return None


def _credentials():
    """Saved sign-in, refreshed if needed; None if not signed in."""
    if not TOKEN.exists():
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
            _save(creds)
        return creds if creds.valid else None
    except Exception:
        return None   # revoked or damaged: the user simply signs in again


def _save(creds) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(creds.to_json())
    try:
        os.chmod(TOKEN, 0o600)
    except OSError:
        pass


def sign_in() -> str:
    """Opens Google's sign-in page in the browser; returns the signed-in email."""
    cfg = client_config()
    if not cfg:
        raise RuntimeError("Calendar sign-in isn't set up in this version of the app.")
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_config(cfg, SCOPES)
    creds = flow.run_local_server(
        port=0, open_browser=True, prompt="select_account",
        success_message="Signed in. You can close this tab and go back to PBD Call Copilot.")
    _save(creds)
    return signed_in_email() or ""


def sign_out() -> None:
    creds = _credentials()
    if creds:
        try:
            from google.auth.transport.requests import AuthorizedSession
            AuthorizedSession(creds).post("https://oauth2.googleapis.com/revoke",
                                          params={"token": creds.token}, timeout=10)
        except Exception:
            pass
    TOKEN.unlink(missing_ok=True)


def signed_in_email() -> Optional[str]:
    creds = _credentials()
    if not creds:
        return None
    from google.auth.transport.requests import AuthorizedSession
    try:
        r = AuthorizedSession(creds).get("https://openidconnect.googleapis.com/v1/userinfo", timeout=10)
        return r.json().get("email") if r.ok else None
    except Exception:
        return None


# -- meetings ------------------------------------------------------------------
def fetch_events(now: Optional[dt.datetime] = None) -> List[Dict]:
    """Timed events from every visible calendar, 7 days back to 7 days ahead,
    one entry per meeting even when it sits on several calendars."""
    creds = _credentials()
    if not creds:
        return []
    from google.auth.transport.requests import AuthorizedSession
    session = AuthorizedSession(creds)
    now = now or dt.datetime.now(dt.timezone.utc)
    lo = (now - dt.timedelta(days=LOOK_BACK_DAYS)).isoformat()
    hi = (now + dt.timedelta(days=LOOK_AHEAD_DAYS)).isoformat()
    cals = session.get(f"{API}/users/me/calendarList", params={"minAccessRole": "reader"}, timeout=15).json()
    merged: Dict[str, Dict] = {}
    for cal in cals.get("items", []):
        if cal.get("hidden") or cal.get("selected") is False:
            continue
        r = session.get(f"{API}/calendars/{cal['id']}/events", timeout=15, params={
            "timeMin": lo, "timeMax": hi, "singleEvents": "true", "orderBy": "startTime", "maxResults": 250})
        if not r.ok:
            continue
        for ev in r.json().get("items", []):
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
