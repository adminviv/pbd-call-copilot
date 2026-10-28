"""PBD's own knowledge base: who PBD is, its people and affiliates, a plain-words glossary.

Private: never in this repository. The app download carries it locked (knowledge.enc,
see kbcrypt.py) and unlocks it with the PBD key on this computer; on a PBD computer it
can also be a plain ~/.pbd-copilot/knowledge.md. Built from Drive, Otter, the WhatsApp export, Gmail
and Asana, and reviewed by the PBD team.

Two parts are used on a call:
- base(): everything except the people entries, sent with every request
  (framing rules, about PBD, team, glossary, unconfirmed facts)
- for_call(text): only the people/organization entries named in the meeting
  invite or the conversation so far, so a call with Regeneron brings in Regeneron's history
"""
from __future__ import annotations

import logging
import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import List, Tuple

PEOPLE = "## People and organizations"
MAX_ENTRIES = 8


def _pbd_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    env = Path.home() / ".pbd.env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "ANTHROPIC_API_KEY":
                key = value.strip().strip('"')
    return key


def _read() -> str:
    """Override file, else the locked copy inside the app, else the plain copy on this computer."""
    env = os.environ.get("COPILOT_KNOWLEDGE")
    if env and Path(env).exists():
        return Path(env).read_text(encoding="utf-8")
    bundled = getattr(sys, "_MEIPASS", None)
    locked = Path(bundled) / "knowledge.enc" if bundled else None
    if locked and locked.exists():
        try:
            import kbcrypt
            return kbcrypt.unlock(locked.read_bytes(), _pbd_key())
        except Exception as e:   # no key yet / different key: work without it, but say so in the log
            logging.getLogger("copilot").warning("knowledge base not unlocked: %s", e)
    plain = Path.home() / ".pbd-copilot" / "knowledge.md"
    return plain.read_text(encoding="utf-8") if plain.exists() else ""


@lru_cache(maxsize=1)
def _load() -> Tuple[str, List[Tuple[str, List[str], str]]]:
    """(base text, [(name, names to match, entry text)])"""
    text = _read()
    if not text:
        return "", []
    start = text.find(PEOPLE)
    if start < 0:
        return text, []
    end = text.find("\n## ", start + len(PEOPLE))
    people = text[start:end if end > 0 else len(text)]
    base = text[:start] + (text[end:] if end > 0 else "")
    entries = []
    for block in re.split(r"\n(?=### )", people)[1:]:
        heading = block.splitlines()[0][4:]
        name = heading.split(" — ")[0].strip()
        also = re.search(r"^Also called:\s*(.+)$", block, re.M)
        names = [name] + ([a.strip() for a in also.group(1).split(",")] if also else [])
        entries.append((name, _match_terms(names), block.strip()))
    return base.strip(), entries


def _match_terms(names: List[str]) -> List[str]:
    """Full names plus distinctive surnames ("Bonkowsky"), never short or common words."""
    out = []
    for n in names:
        n = re.sub(r"\s*\([^)]*\)", "", n).strip()
        if len(n) >= 4:
            out.append(n.lower())
        parts = n.split()
        if len(parts) >= 2 and len(parts[-1]) >= 5 and parts[-1][0].isupper():
            out.append(parts[-1].lower())
    return out


def base() -> str:
    return _load()[0]


def for_call(*texts: str) -> str:
    """Entries for the people/organizations named in the texts, earlier texts first
    (pass the meeting invite before the conversation, so invitees always make the cut)."""
    hits: List[str] = []
    for text in texts:
        low = " " + re.sub(r"\s+", " ", (text or "").lower()) + " "
        for name, terms, entry in _load()[1]:
            if len(hits) >= MAX_ENTRIES:
                break
            if entry not in hits and any(re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", low) for t in terms):
                hits.append(entry)
    return "\n\n".join(hits)
