"""Read-only search over the local knowledge bases.

Both sources are COPIES in ~/pbd-corpus-repo; they are opened with SQLite's
read-only mode so nothing here can ever modify them.

- PeroxiOS: 3,219 PMC open-access peroxisome papers (FTS5 index).
- PEX10 RAG (Adam Freygang / ARIA, HuggingFace SkyWhal3/PEX10-RAG-Nemotron):
  835 PMC-OA papers as ~99k text chunks plus ClinVar/variant cards. Searched
  lexically through Chroma's built-in trigram FTS table; the embedding index
  is not used (it needs the Nemotron embedder and the skipped .pickle files).
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Dict, List

import os

# Andrew's PC can point this elsewhere with COPILOT_DATA; a missing source is skipped.
REPO = Path(os.environ.get("COPILOT_DATA", Path.home() / "pbd-corpus-repo"))
PEROXIOS_DB = REPO / "index" / "corpus.sqlite3"
PEX10_DB = REPO / "huggingface" / "SkyWhal3" / "PEX10-RAG-Nemotron" / "chroma.sqlite3"


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _clean(term: str) -> str:
    return re.sub(r'["*^:()]', " ", term).strip()


def _fts_query(terms: List[str]) -> str:
    """Each term matches when all its words appear (any order); terms are OR'ed."""
    groups = []
    for t in terms:
        words = [w for w in _clean(t).split() if len(w) >= 3]
        if words:
            groups.append("(" + " AND ".join(f'"{w}"' for w in words) + ")")
    return " OR ".join(groups)


def search_peroxios(terms: List[str], limit: int = 6) -> List[Dict]:
    """Papers matching ANY of the given phrases, best BM25 rank first."""
    query = _fts_query(terms)
    if not query or not PEROXIOS_DB.exists():
        return []
    with _ro(PEROXIOS_DB) as conn:
        rows = conn.execute(
            "SELECT pmc_id, pmid, doi, title, authors, "
            "substr(abstract, 1, 1500) || char(10) || '… ' || snippet(papers, 5, '', '', ' … ', 60) "
            "FROM papers WHERE papers MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
    return [
        {"source": "PeroxiOS", "pmcid": r[0], "pmid": r[1], "doi": r[2],
         "title": r[3], "authors": r[4], "text": r[5]}
        for r in rows
    ]


def search_pex10(terms: List[str], limit: int = 6) -> List[Dict]:
    """Paper chunks from Adam's PEX10 corpus (skips the ClinVar cards)."""
    query = _fts_query(terms)
    if not query or not PEX10_DB.exists():
        return []
    out: List[Dict] = []
    seen = set()
    with _ro(PEX10_DB) as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT rowid FROM embedding_fulltext_search "
            "WHERE embedding_fulltext_search MATCH ? ORDER BY rank LIMIT ?",
            (query, limit * 8),
        )]
        for rid in ids:
            meta = dict(conn.execute(
                "SELECT key, coalesce(string_value, cast(int_value as text), "
                "cast(float_value as text)) FROM embedding_metadata WHERE id = ?",
                (rid,),
            ).fetchall())
            pmcid = meta.get("pmcid")
            if not pmcid or pmcid in seen:
                continue
            seen.add(pmcid)
            out.append({
                "source": "PEX10 RAG", "pmcid": pmcid, "pmid": None,
                "doi": meta.get("doi"), "title": meta.get("title"),
                "authors": meta.get("authors"), "year": meta.get("year"),
                "text": (meta.get("chroma:document") or "")[:900],
            })
            if len(out) >= limit:
                break
    return out


def search_all(terms: List[str]) -> List[Dict]:
    """Merge both sources, one entry per paper (PeroxiOS wins on duplicates)."""
    merged: Dict[str, Dict] = {}
    for hit in search_peroxios(terms) + search_pex10(terms):
        merged.setdefault(hit["pmcid"], hit)
    return list(merged.values())
