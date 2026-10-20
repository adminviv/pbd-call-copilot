"""Replay a recorded call through the Copilot using the live app's exact rules.

    python3 replay_live.py transcripts/<call>.txt --out results/retest/<config>

Unlike replay.py (fixed 2-minute windows), this follows live/session.py:
  - speakers are only "Andrew" and "Them" (the app can't know names)
  - a line is available ~12 s after it was spoken (measured speech-to-text delay)
  - every 5 s, if 75 s have passed since the last analysis and 60+ new words
    were said, the new stretch goes to copilot.suggest with everything before it
  - the call clock keeps running while Claude thinks, so "shown at" is when the
    cards would really appear on Andrew's screen
  - at most 2 cards per round; a final forced round when the call ends

Which data the engine sees is set by the environment, as on a real PC:
  COPILOT_DATA (paper indexes) and COPILOT_PROFILE (private briefing).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "live"))
import copilot  # noqa: E402
from session import MAX_CARDS, MIN_NEW_WORDS, SUGGEST_EVERY_S  # noqa: E402

LINE = re.compile(r"^\[(\d+):(\d+):(\d+)\] ([^:]+): (.*)$")
STT_DELAY_S = 12   # measured: a spoken line reaches the transcript ~12 s later
TICK_S = 5         # session._suggest_loop checks every 5 s


def load(path: Path):
    lines = []
    for raw in path.read_text().splitlines():
        m = LINE.match(raw)
        if m:
            h, mi, s, who, text = m.groups()
            who = "Andrew" if who.strip().lower().startswith("andrew longenecker") else "Them"
            lines.append({"t": int(h) * 3600 + int(mi) * 60 + int(s), "speaker": who, "text": text})
    return lines


def clock(t: float) -> str:
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


def render(lines):
    return "\n".join(f"[{clock(l['t'])}] {l['speaker']}: {l['text']}" for l in lines)


def run(path: Path, out_dir: Path) -> dict:
    lines = load(path)
    end = lines[-1]["t"] + STT_DELAY_S
    now, last, processed, shown, rounds = lines[0]["t"], 0.0, 0, [], []
    cost0, calls0 = copilot.cost_so_far(), copilot.USAGE["calls"]

    def analyse(force: bool) -> None:
        nonlocal now, last, processed
        available = [l for l in lines if l["t"] + STT_DELAY_S <= now]
        new, history = available[processed:], available[:processed]
        if not new or (not force and sum(len(l["text"].split()) for l in new) < MIN_NEW_WORDS):
            return
        last = now
        processed += len(new)
        started = time.time()
        try:
            res = copilot.suggest(render(new), render(history), shown)
            error = ""
        except Exception as e:  # the live app shows an error and carries on
            res, error = {"cards": [], "dropped": []}, f"{type(e).__name__}: {e}"
        took = time.time() - started
        now += took
        cards = res.get("cards", [])[:MAX_CARDS]
        shown.extend(c["ask"] for c in cards)
        rounds.append({
            "heard_until": clock(new[-1]["t"]), "shown_at": clock(now), "seconds": round(took, 1),
            "topic": res.get("topic"), "in_plain_words": res.get("in_plain_words", ""),
            "worth_suggesting": bool(res.get("search_terms")) or bool(cards),
            "papers_found": res.get("papers_found", 0), "cards": cards,
            "dropped": res.get("dropped", []), "error": error,
        })
        print(f"  {clock(new[-1]['t']):>6} -> shown {clock(now):>6}  {len(cards)} card(s)  "
              f"{res.get('papers_found', 0):>2} papers  {took:4.0f}s  {res.get('topic') or error}", flush=True)

    while now < end:
        if now - last >= SUGGEST_EVERY_S:
            analyse(force=False)
        now += TICK_S
    analyse(force=True)   # Andrew switches off at the end of the call

    summary = {
        "call": path.stem, "minutes": round((lines[-1]["t"] - lines[0]["t"]) / 60),
        "rounds": len(rounds), "rounds_with_cards": sum(1 for r in rounds if r["cards"]),
        "cards": sum(len(r["cards"]) for r in rounds),
        "science_cards": sum(1 for r in rounds for c in r["cards"] if c.get("kind") == "science"),
        "next_step_cards": sum(1 for r in rounds for c in r["cards"] if c.get("kind") == "next_step"),
        "cards_with_paper": sum(1 for r in rounds for c in r["cards"] if c.get("paper")),
        "first_card_at": next((r["shown_at"] for r in rounds if r["cards"]), None),
        "errors": sum(1 for r in rounds if r["error"]),
        "dropped": sum(len(r["dropped"]) for r in rounds),
        "median_seconds": sorted(r["seconds"] for r in rounds)[len(rounds) // 2] if rounds else None,
        "claude_calls": copilot.USAGE["calls"] - calls0,
        "cost_usd": round(copilot.cost_so_far() - cost0, 2),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{path.stem}.json").write_text(json.dumps({"summary": summary, "rounds": rounds}, indent=1))
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("transcripts", nargs="+")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    for t in args.transcripts:
        print(Path(t).stem, flush=True)
        print(json.dumps(run(Path(t), Path(args.out))), flush=True)
