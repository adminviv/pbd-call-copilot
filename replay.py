"""Replay a recorded call through the Copilot as if it were live.

    python3 replay.py transcripts/<call>.txt [--windows N]

Splits the transcript into ~2-minute windows, runs each through copilot.suggest,
times it, and writes results/<name>.json plus a readable results/<name>.md.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import copilot

LINE = re.compile(r"^\[(\d+):(\d+):(\d+)\] ([^:]+): (.*)$")
WINDOW_SECONDS = 120


def load(path: Path):
    lines = []
    for raw in path.read_text().splitlines():
        m = LINE.match(raw)
        if m:
            h, mi, s, who, text = m.groups()
            lines.append((int(h) * 3600 + int(mi) * 60 + int(s), who, text))
    return lines


def windows(lines):
    current, start = [], None
    for t, who, text in lines:
        start = t if start is None else start
        current.append((t, who, text))
        if t - start >= WINDOW_SECONDS:
            yield current
            current, start = [], None
    if current:
        yield current


def fmt(t):
    return f"{t // 60}:{t % 60:02d}"


def render(lines):
    return "\n".join(f"[{fmt(t)}] {who}: {text}" for t, who, text in lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript")
    ap.add_argument("--windows", type=int, default=0, help="stop after N windows (0 = all)")
    args = ap.parse_args()

    path = Path(args.transcript)
    lines = load(path)
    shown, log, history = [], [], ""
    for i, win in enumerate(windows(lines), 1):
        if args.windows and i > args.windows:
            break
        text = render(win)
        t0 = time.time()
        res = copilot.suggest(text, history, shown)
        res.update({"window": i, "at": fmt(win[-1][0]), "seconds": round(time.time() - t0, 1)})
        shown += [c["ask"] for c in res["cards"]]
        history += "\n" + text
        log.append(res)
        print(f"[{res['at']}] {res['seconds']}s  {res['topic']}  -> {len(res['cards'])} card(s), "
              f"{len(res['dropped'])} dropped")

    out = Path("results")
    out.mkdir(exist_ok=True)
    (out / f"{path.stem}.json").write_text(json.dumps(log, indent=2))
    md = [f"# Copilot replay: {path.stem}\n"]
    for r in log:
        md.append(f"## {r['at']} — {r['topic']}  _({r['seconds']}s)_")
        if r.get("in_plain_words"):
            md.append(f"> **In plain words:** {r['in_plain_words']}\n")
        for c in r["cards"]:
            p = c["paper"]
            label = "Ask" if c["kind"] == "science" else "Next step for PBD"
            md.append(f"- **{label}:** {c['ask']}\n  - *What this means for PBD:* {c['why']}")
            for t in c.get("terms", []):
                md.append(f"  - *{t['term']}* = {t['plain']}")
            if p:
                md.append(f"  - Paper: {p['first_author']} et al. {p['year']}, *{p['journal']}* — {p['title']} "
                          f"(PMID {p['pmid']}, {c['pmcid']}, from {p['found_in']})")
            if c["connection"]:
                md.append(f"  - Connection: {c['connection']}")
        for d in r["dropped"]:
            md.append(f"- ~~{d['card']['ask']}~~ — dropped: {d['reason']}")
        if not r["cards"] and not r["dropped"]:
            md.append("- (no suggestion)")
        md.append("")
    (out / f"{path.stem}.md").write_text("\n".join(md))
    u = copilot.USAGE
    summary = (f"{u['calls']} Claude calls, {u['input_tokens']:,} input + {u['output_tokens']:,} output tokens, "
               f"about ${copilot.cost_so_far():.2f}")
    (out / f"{path.stem}.md").write_text("\n".join(md) + f"\n---\nRun cost: {summary}\n")
    print(f"\nwrote results/{path.stem}.md  ({summary})")


if __name__ == "__main__":
    main()
