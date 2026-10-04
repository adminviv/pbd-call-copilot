"""One call: audio -> transcript -> suggestions, all in background threads.

The UI only ever sees plain dicts through the `events` callback:
  {"type": "status",     ...}            listening / paused / model info
  {"type": "line",       speaker,t,text} a new transcript line
  {"type": "suggestions", topic, in_plain_words, cards}
  {"type": "error",      message}
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # engine lives one folder up

import copilot  # noqa: E402
from audio import FileSource, WindowsSource  # noqa: E402
from transcriber import LiveTranscriber  # noqa: E402

SUGGEST_EVERY_S = 75      # how often a new stretch of conversation is analysed
MIN_NEW_WORDS = 60        # skip if little was said since last time
MAX_CARDS = 2             # Andrew: "2 sounds good"
TRANSCRIPTS = Path.home() / "Documents" / "PBD Call Copilot" / "transcripts"


class CallSession:
    def __init__(self, events: Callable[[Dict], None], test_wav: Optional[str] = None):
        self.events = events
        self.test_wav = test_wav
        self.lines: List[Dict] = []
        self.shown_asks: List[str] = []
        self.audio_q: "queue.Queue" = queue.Queue()
        self.sources = []
        self.running = False
        self.started = time.time()
        self.last_suggest_at = 0.0
        self.processed = 0  # transcript lines already analysed
        self.lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.started = time.time()
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self.running = False
        for s in self.sources:
            s.stop()
        self.sources = []
        self._save()
        self.events({"type": "status", "state": "off"})

    # -- workers ---------------------------------------------------------
    def _run(self) -> None:
        try:
            self.events({"type": "status", "state": "loading"})
            tr = LiveTranscriber(self._on_line)
            self.events({"type": "status", "state": "listening", "model": tr.model_name})
            if self.test_wav:
                self.sources = [FileSource("Them", self.audio_q, self.started, self.test_wav)]
            else:
                self.sources = [
                    WindowsSource("Andrew", self.audio_q, self.started, loopback=False),
                    WindowsSource("Them", self.audio_q, self.started, loopback=True),
                ]
            for s in self.sources:
                s.start()
            threading.Thread(target=self._suggest_loop, daemon=True).start()
            while self.running:
                try:
                    tr.feed(self.audio_q.get(timeout=0.5))
                except queue.Empty:
                    if self.test_wav and not any(s.is_alive() for s in self.sources):
                        tr.flush_all()
                        self._suggest_now(force=True)
                        self.stop()
                        return
            tr.flush_all()
        except Exception as e:  # surface, don't crash the window
            self.events({"type": "error", "message": f"{type(e).__name__}: {e}"})
            self.running = False

    def _on_line(self, line: Dict) -> None:
        with self.lock:
            self.lines.append(line)
        self.events({"type": "line", **line})

    def _suggest_loop(self) -> None:
        while self.running:
            time.sleep(5)
            if time.time() - self.last_suggest_at >= SUGGEST_EVERY_S:
                self._suggest_now()

    def _suggest_now(self, force: bool = False) -> None:
        with self.lock:
            new = self.lines[self.processed:]
            history = self.lines[:self.processed]
        words = sum(len(l["text"].split()) for l in new)
        if not force and words < MIN_NEW_WORDS:
            return
        self.last_suggest_at = time.time()
        if not new:
            return
        self.processed += len(new)
        try:
            res = copilot.suggest(_render(new), _render(history), self.shown_asks)
        except Exception as e:
            self.events({"type": "error", "message": f"Suggestion step failed: {e}"})
            return
        cards = res.get("cards", [])[:MAX_CARDS]
        self.shown_asks += [c["ask"] for c in cards]
        self.events({"type": "suggestions", "topic": res.get("topic"),
                     "in_plain_words": res.get("in_plain_words", ""), "cards": cards,
                     "at": _clock(new[-1]["t"])})

    def _save(self) -> None:
        if not self.lines:
            return
        TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
        path = TRANSCRIPTS / f"{datetime.now():%Y-%m-%d_%H%M}_call.txt"
        path.write_text(_render(self.lines))
        self.events({"type": "status", "state": "saved", "path": str(path)})


def _clock(t: float) -> str:
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


def _render(lines: List[Dict]) -> str:
    return "\n".join(f"[{_clock(l['t'])}] {l['speaker']}: {l['text']}" for l in lines)
