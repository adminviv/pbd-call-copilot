"""Citation check: a suggested paper is only shown if NCBI confirms it exists
and its title matches the one we retrieved. Free public API, no key needed.
"""
from __future__ import annotations

import json
import re
import urllib.request
from functools import lru_cache
from typing import Optional

ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pmc&retmode=json&id={}"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


@lru_cache(maxsize=512)
def ncbi_record(pmcid: str) -> Optional[dict]:
    num = pmcid.upper().replace("PMC", "")
    if not num.isdigit():
        return None
    try:
        with urllib.request.urlopen(ESUMMARY.format(num), timeout=15) as r:
            data = json.load(r)
    except Exception:
        return None
    rec = data.get("result", {}).get(num)
    if not rec or "error" in rec:
        return None
    pmid = next((a.get("value") for a in rec.get("articleids", []) if a.get("idtype") == "pmid"), None)
    return {"title": rec.get("title", ""), "pmid": pmid, "journal": rec.get("fulljournalname") or rec.get("source"),
            "year": (rec.get("pubdate") or "")[:4], "first_author": (rec.get("authors") or [{}])[0].get("name")}


def verify(pmcid: str, expected_title: str) -> Optional[dict]:
    """Return the NCBI record if the paper exists and titles match, else None."""
    rec = ncbi_record(pmcid)
    if not rec:
        return None
    a, b = _norm(rec["title"]), _norm(expected_title)
    if not a or not b:
        return None
    # tolerate punctuation / truncation differences, not a different paper
    if a[:60] != b[:60] and a not in b and b not in a:
        return None
    return rec
