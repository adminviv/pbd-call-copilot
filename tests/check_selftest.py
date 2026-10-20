"""Pass/fail for the self-test report written by `app.py --self-test`.

    python tests/check_selftest.py report.json

The release is blocked unless every check passes. Each check is something a
user would notice on a real call.
"""
import json
import sys

MAX_START_S = 60       # switched on -> "Listening"
MIN_LINES = 8          # the 2-minute test call has 12 turns
MAX_LAG_S = 25         # a spoken segment (up to 12 s long) must be on screen within this
MUST_HEAR = ["pex5", "peroxisome", "pex1"]


def main(path: str) -> int:
    r = json.load(open(path))
    run = [e for e in r["events"] if e["phase"] == "run"]
    after = [e for e in r["events"] if e["phase"] == "off-check"]
    lines = [e for e in run if e["type"] == "line"]
    listening = next((e for e in run if e.get("state") == "listening"), None)
    t0 = r.get("session_started") or r["started_at"]
    lags = [e["at"] - t0 - e["t"] for e in lines]
    heard = " ".join(e["text"] for e in lines).lower()
    suggestions = [e for e in run if e["type"] == "suggestions"]
    cards = [c for e in suggestions for c in e.get("cards", [])]

    checks = [
        ("finished without timing out", not r["timed_out"], ""),
        ("no errors on screen", not [e for e in run if e["type"] == "error"],
         "; ".join(e["message"] for e in run if e["type"] == "error")),
        (f"listening within {MAX_START_S} s", bool(listening) and listening["at"] - r["started_at"] <= MAX_START_S,
         f"{listening['at'] - r['started_at']:.0f} s" if listening else "never"),
        (f"at least {MIN_LINES} transcript lines", len(lines) >= MIN_LINES, f"{len(lines)} lines"),
        (f"transcript keeps up (<= {MAX_LAG_S} s behind)", bool(lags) and max(lags) <= MAX_LAG_S,
         f"worst {max(lags):.0f} s" if lags else "no lines"),
        ("science words heard correctly", all(w in heard for w in MUST_HEAR),
         ", ".join("missing " + w for w in MUST_HEAR if w not in heard)),
        ("sound bars get real levels", any(e["type"] == "level" and e["level"] > 0.1 for e in run), ""),
        ("off during start-up stops everything",
         not [e for e in after if e["type"] in ("line", "suggestions") or e.get("state") == "listening"], ""),
    ]
    if not r["offline"]:
        checks += [
            ("suggestions appear", bool(suggestions), f"{len(suggestions)} rounds"),
            ("at least one question card", bool(cards), f"{len(cards)} cards"),
            ("cards cite a verified paper", any(c.get("paper") and c["paper"].get("pmid") for c in cards), ""),
        ]

    failed = 0
    for name, ok, detail in checks:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed" + ("" if r["offline"] else " (with Claude)"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
