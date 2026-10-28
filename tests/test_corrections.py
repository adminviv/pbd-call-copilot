"""Checks for live/corrections.py.  python tests/test_corrections.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "live"))
from corrections import fix  # noqa: E402

CASES = [
    ("very specific bio chemistry peroxy some raw stuff", "very specific bio chemistry peroxisome raw stuff"),
    ("the paroxysms were empty", "the peroxisomes were empty"),
    ("paroxysomal disorders", "peroxisomal disorders"),
    ("paroxysmal disorder", "peroxisomal disorder"),
    ("she had paroxysmal seizures", "she had paroxysmal seizures"),   # a real medical word: leave it
    ("we loaded it into Proxy OS", "we loaded it into PeroxiOS"),
    ("pex 10 and Pex5", "PEX10 and PEX5"),
    ("PECs one", "PEX1"),
    ("To PEX-5 binds cargo, then PEC. 5 gets stuck", "To PEX5 binds cargo, then PEX5 gets stuck"),
    ("how peroxysms import", "how peroxisomes import"),
    ("plasmallogen levels drop", "plasmalogen levels drop"),
    ("Which isn't AAA at pace", "Which isn't AAA ATPase"),
    ("zell weger spectrum", "Zellweger spectrum"),
    ("the PVD Project", "the PBD Project"),
    ("I'm Andrew Lonnecker", "I'm Andrew Longenecker"),
    ("a proxy for the outcome", "a proxy for the outcome"),          # ordinary words stay
]
failed = 0
for heard, want in CASES:
    got = fix(heard)
    ok = got == want
    failed += not ok
    print(("PASS  " if ok else "FAIL  ") + f"{heard!r} -> {got!r}" + ("" if ok else f" (wanted {want!r})"))
print(f"\n{len(CASES) - failed}/{len(CASES)} checks passed")
sys.exit(1 if failed else 0)
