"""Checks for orgkb.py (PBD knowledge base) with a made-up file.  python tests/test_orgkb.py"""
import os
import sys
import tempfile
from pathlib import Path

KB = """# PBD Project knowledge base
## How Andrew talks about PBD
- Research accelerator, not a charity.
## People and organizations
### Ada Example — Example University, lab head
Also called: Example lab, Dr. Ada
Works on assay X. Last call 2026-09.
Sources: Otter 2026-09-01
### Bob Sample — Sample Foundation
Funder. Last email 2026-10.
### Example Pharma — biotech company
Also called: ExPharma
Talked about models.
## Plain-words glossary
Peroxisome = a small cell part that breaks down certain fats.
"""
f = Path(tempfile.mkdtemp()) / "knowledge.md"
f.write_text(KB, encoding="utf-8")
os.environ["COPILOT_KNOWLEDGE"] = str(f)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orgkb  # noqa: E402

failed = 0


def check(name, ok):
    global failed
    failed += not ok
    print(("PASS  " if ok else "FAIL  ") + name)


base = orgkb.base()
check("base keeps framing and glossary", "not a charity" in base and "Peroxisome =" in base)
check("base leaves out people entries", "Ada Example" not in base)
check("invitee by full name", "Ada Example" in orgkb.for_call("Meeting: Ada Example <> Andrew"))
check("surname alone matches", "Ada Example" in orgkb.for_call("so as Example said earlier"))
check("alias matches", "Example Pharma" in orgkb.for_call("we spoke with ExPharma last week"))
check("nobody named, nothing added", orgkb.for_call("hello, how are you?") == "")
check("no match inside other words", orgkb.for_call("we sampled cells") == "")
orgkb.MAX_ENTRIES = 1
check("invitees come first when capped", orgkb.for_call("Bob Sample", "Ada Example") .startswith("### Bob Sample"))
print(f"\n{8 - failed}/8 checks passed")
sys.exit(1 if failed else 0)
