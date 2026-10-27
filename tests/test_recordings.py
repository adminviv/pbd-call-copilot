"""Checks for live/recordings.py (past calls).  python tests/test_recordings.py"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "live"))
import recordings  # noqa: E402

recordings.FOLDER = Path(tempfile.mkdtemp()) / "calls"
failed = 0


def check(name, ok):
    global failed
    failed += not ok
    print(("PASS  " if ok else "FAIL  ") + name)


check("empty list when nothing saved", recordings.list_all() == [])
base = {"title": "Test call", "attendees": ["Kris"], "started": "2026-10-15T09:00:00", "minutes": 27,
        "topic": "Assays", "lines": [{"speaker": "Them", "t": 1, "text": "hello"}], "plain": [],
        "cards": [{"ask": "A?", "paper": {"pmid": "1"}}, {"ask": "B?"}], "starred": ["A?"]}
recordings.save({**base, "id": "20261015-090000"})
recordings.save({**base, "id": "20261016-090000", "title": "Later call"})
items = recordings.list_all()
check("newest first", [i["id"] for i in items] == ["20261016-090000", "20261015-090000"])
check("counts cards, papers, saved", (items[0]["cards"], items[0]["papers"], items[0]["starred"]) == (2, 1, 1))
check("list leaves out the transcript", "lines" not in items[0])
check("load returns the transcript", recordings.load("20261015-090000")["lines"][0]["text"] == "hello")
check("unstar", recordings.toggle_star("20261015-090000", "A?")["starred"] == [])
check("star", recordings.toggle_star("20261015-090000", "B?")["starred"] == ["B?"])
check("bad id cannot escape the folder", recordings.load("../../etc/passwd") is None
      and recordings.toggle_star("../x", "A?") is None)
print(f"\n{8 - failed}/8 checks passed")
sys.exit(1 if failed else 0)
