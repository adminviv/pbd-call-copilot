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
    # Off is pressed ~0.05 s after On; once the "off" status is out, nothing new may appear.
    off_at = next((e["at"] for e in after if e.get("state") == "off"), after[-1]["at"] if after else 0)
    leaked = [e for e in after if e["at"] > off_at and
              (e["type"] in ("line", "suggestions") or e.get("state") == "listening")]
    cards = [c for e in suggestions for c in e.get("cards", [])]
    netcheck = [e for e in r["events"] if e["type"] == "netcheck"]

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
        ("secure connections work (Google, Europe PMC, NCBI) and sign-in client is bundled",
         bool(netcheck) and all(netcheck[0].get(k) for k in ("google", "europepmc", "ncbi", "calendar_client")),
         ", ".join(k for k in ("google", "europepmc", "ncbi", "calendar_client")
                   if not (netcheck and netcheck[0].get(k))) or ""),
        ("off during start-up stops everything", not leaked,
         "; ".join(f"{e['type']} {e.get('state') or e.get('text', '')[:40]} at +{e['at'] - off_at:.1f}s"
                   for e in leaked)),
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
