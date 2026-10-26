"""Checks the confirmed-meeting rule without any Google account.

    python tests/test_gcal.py
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "live"))
import gcal  # noqa: E402

CAL = "owner@example.org"
ZOOM = "https://us02web.zoom.us/j/123"


def event(**kw):
    ev = {"status": "confirmed", "summary": "Lab call",
          "start": {"dateTime": "2026-10-28T15:00:00Z"}, "end": {"dateTime": "2026-10-28T15:30:00Z"},
          "organizer": {"email": "researcher@uni.edu"},
          "attendees": [{"email": CAL, "responseStatus": "accepted"},
                        {"email": "researcher@uni.edu", "responseStatus": "accepted"}],
          "location": ZOOM}
    ev.update(kw)
    return ev


def check(name, got, want):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  (got {got!r}, want {want!r})"))
    return ok


results = [
    check("accepted Zoom call with 2 people is confirmed", gcal.is_confirmed(event(), CAL), (True, "")),
    check("cancelled event", gcal.is_confirmed(event(status="cancelled"), CAL)[0], False),
    check("only one person", gcal.is_confirmed(event(attendees=[{"email": CAL, "responseStatus": "accepted"}]), CAL)[1],
          "fewer than 2 people invited"),
    check("declined", gcal.is_confirmed(event(attendees=[{"email": CAL, "responseStatus": "declined"},
                                                          {"email": "x@y.org"}]), CAL)[1], "declined"),
    check("no reply yet", gcal.is_confirmed(event(attendees=[{"email": CAL, "responseStatus": "needsAction"},
                                                              {"email": "x@y.org"}]), CAL)[1], "not accepted yet"),
    check("organiser counts as accepted",
          gcal.is_confirmed(event(organizer={"email": CAL}, attendees=[{"email": CAL}, {"email": "x@y.org"}]), CAL)[0], True),
    check("no video link", gcal.is_confirmed(event(location="Room 4"), CAL)[1], "no video-call link"),
    check("Google Meet link counts", gcal.is_confirmed(event(location="", hangoutLink="https://meet.google.com/abc"), CAL)[0], True),
    check("Teams link in description counts",
          gcal.is_confirmed(event(location="", description="Join: https://teams.microsoft.com/l/meetup"), CAL)[0], True),
    check("meeting rooms are not people",
          gcal.is_confirmed(event(attendees=[{"email": CAL, "responseStatus": "accepted"},
                                             {"email": "room@res.org", "resource": True}]), CAL)[0], False),
]

m = gcal.summarize(event(), CAL, "Owner")
at = lambda h, mi: dt.datetime(2026, 10, 28, h, mi, tzinfo=dt.timezone.utc)  # noqa: E731
results += [
    check("switch not allowed 11 min before start", gcal.active_meeting([m], at(14, 49)), None),
    check("switch allowed 10 min before start", gcal.active_meeting([m], at(14, 50)) is m, True),
    check("switch allowed during the meeting", gcal.active_meeting([m], at(15, 20)) is m, True),
    check("switch allowed 15 min after the end", gcal.active_meeting([m], at(15, 45)) is m, True),
    check("switch not allowed 16 min after the end", gcal.active_meeting([m], at(15, 46)), None),
    check("unconfirmed meeting never allows the switch",
          gcal.active_meeting([gcal.summarize(event(location="Room 4"), CAL, "Owner")], at(15, 10)), None),
    check("meeting context names title and people", "Lab call" in gcal.meeting_context(m) and "researcher" in gcal.meeting_context(m), True),
]

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
