"""Past calls ("Recordings"), saved on this computer only.

Audio is never kept. Each call is one JSON file in Documents/PBD Call Copilot/calls
with the meeting, the transcript, the "in plain words" notes, every card shown
and the questions Andrew saved for follow-up.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

FOLDER = Path.home() / "Documents" / "PBD Call Copilot" / "calls"


def _path(call_id: str) -> Optional[Path]:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}", call_id or ""):
        return None   # ids come from the panel: never let one point outside the folder
    return FOLDER / f"{call_id}.json"


def save(record: Dict) -> str:
    FOLDER.mkdir(parents=True, exist_ok=True)
    _path(record["id"]).write_text(json.dumps(record, indent=1), encoding="utf-8")
    return record["id"]


def load(call_id: str) -> Optional[Dict]:
    p = _path(call_id)
    if not p or not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_all() -> List[Dict]:
    """Newest first, without the transcript (the list only needs the summary)."""
    out = []
    for p in sorted(FOLDER.glob("*.json"), reverse=True) if FOLDER.exists() else []:
        r = load(p.stem)
        if not r:
            continue
        out.append({k: r.get(k) for k in ("id", "title", "attendees", "started", "minutes", "topic")} |
                   {"cards": len(r.get("cards", [])), "papers": sum(1 for c in r.get("cards", []) if c.get("paper")),
                    "starred": len(r.get("starred", []))})
    return out


def toggle_star(call_id: str, ask: str) -> Optional[Dict]:
    r = load(call_id)
    if not r:
        return None
    starred = r.setdefault("starred", [])
    if ask in starred:
        starred.remove(ask)
    else:
        starred.append(ask)
    save(r)
    return r
