"""Fix science words the speech model writes by sound.

Parakeet hears "peroxisome" correctly but, not knowing the word, writes "paroxysm"
or "peroxy some". These rules map such sound-alikes back to the PBD term. Measured on
19 minutes of test calls: terms right went from 19% to 79%.

Extra private rules (people's names, from the PBD vocabulary sheet) can be added in
corrections.json next to the app or in ~/.pbd-copilot: [{"heard": "...", "term": "..."}].
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import List, Tuple

RULES: List[Tuple[str, str]] = [
    (r"\b(?:paroxysm|peroxysm|paroxysome|peroxy ?some|peroxi ?some|paroxys|proxisome|peroxisom)(s?)\b", r"peroxisome\1"),
    (r"\b(?:paroxysomal|peroxy ?somal|paroxysmal(?= (?:disorders?|proteins?|import|membrane|biogenesis)))\b",
     "peroxisomal"),
    (r"\b(?:proxy|peroxy|roxy) ?os\b", "PeroxiOS"),
    (r"\b(?:pvd|pdb|bbd|p b d)\b", "PBD"),
    (r"\b(?:pex|pecs|pecks|pec)[ .-]{0,2}(\d{1,2})\b", r"PEX\1"),
    (r"\b(?:pex|pecs|pecks|pec) (one|two|three|five|six|seven|ten|twelve|thirteen|fourteen|sixteen|nineteen)\b",
     lambda m: "PEX" + str(["one", "two", "three", "", "five", "six", "seven", "", "", "ten", "", "twelve",
                            "thirteen", "fourteen", "", "sixteen", "", "", "nineteen"].index(m.group(1).lower()) + 1)),
    (r"\bzell ?weger\b", "Zellweger"),
    (r"\btriple a ?t?p ?ase|\baaa at ?pace\b", "AAA ATPase"),
    (r"\bpexo ?phagy\b", "pexophagy"),
    (r"\bplasma ?l?logens?\b", lambda m: "plasmalogen" + ("s" if m.group(0).lower().endswith("s") else "")),
    (r"\blon(?:g(?:en?)?)? ?n*ecker\b", "Longenecker"),
]


def _private_rules() -> List[Tuple[str, str]]:
    bundled = getattr(sys, "_MEIPASS", None)
    for folder in ([Path(bundled)] if bundled else []) + [Path.home() / ".pbd-copilot"]:
        f = folder / "corrections.json"
        if f.exists():
            try:
                return [(rf"\b{re.escape(r['heard'])}\b", r["term"]) for r in json.loads(f.read_text("utf-8"))]
            except (OSError, ValueError, KeyError, TypeError):
                return []
    return []


_COMPILED = [(re.compile(a, re.I), b) for a, b in RULES + _private_rules()]


def fix(text: str) -> str:
    for pattern, replacement in _COMPILED:
        text = pattern.sub(replacement, text)
    return text
