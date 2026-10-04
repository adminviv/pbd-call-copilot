"""Run a recording through the live pipeline without the window, printing events.

    python live/test_headless.py test_audio/excerpt.wav

Times how long each transcript line and each suggestion takes to appear,
which is what Andrew will feel during a real call.
"""
from __future__ import annotations

import json
import sys
import threading
import time

from session import CallSession

t0 = time.time()
done = threading.Event()


def show(e: dict) -> None:
    stamp = f"[{time.time() - t0:6.1f}s]"
    if e["type"] == "line":
        print(stamp, f"{e['speaker']}: {e['text']}")
    elif e["type"] == "suggestions":
        print(stamp, "SUGGESTIONS", json.dumps({"topic": e["topic"], "plain": e["in_plain_words"],
                                                "asks": [c["ask"] for c in e["cards"]]}, indent=1))
    else:
        print(stamp, e)
        if e["type"] == "status" and e.get("state") == "off":
            done.set()
    sys.stdout.flush()


CallSession(show, test_wav=sys.argv[1]).start()
done.wait(timeout=1800)
