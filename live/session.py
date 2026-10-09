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
from audio import FileSource, MacMicSource, MacSystemSource, WindowsSource  # noqa: E402
from transcriber import LiveTranscriber  # noqa: E402

SUGGEST_EVERY_S = 75      # how often a new stretch of conversation is analysed
MIN_NEW_WORDS = 60        # skip if little was said since last time
MAX_CARDS = 2             # Andrew: "2 sounds good"
MAX_LAG_S = 20            # if speech-to-text falls this far behind, skip ahead to the present
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
        with self.lock:
            self.running = False
            for s in self.sources:
                s.stop()
            self.sources = []
        self._save()
        self.events({"type": "status", "state": "off"})

    def _check_mac_permission(self) -> None:
        """The other side of the call needs macOS's screen & audio recording permission."""
        time.sleep(5)
        sources = self.sources
        if self.running and len(sources) > 1 and not sources[1].is_alive():
            self.events({"type": "error", "message":
                         "Can't hear the other side of the call. Open System Settings > Privacy & Security > "
                         "Screen & System Audio Recording, turn on PBD Call Copilot, then quit and reopen the app."})

    # -- workers ---------------------------------------------------------
    def _run(self) -> None:
        try:
            self.events({"type": "status", "state": "loading"})
            tr = LiveTranscriber(self._on_line)
            with self.lock:
                # Switched off while the speech model was loading: never start the microphone.
                if not self.running:
                    return
                if self.test_wav:
                    self.sources = [FileSource("Them", self.audio_q, self.started, self.test_wav)]
                elif sys.platform == "darwin":
                    self.sources = [
                        MacMicSource("Andrew", self.audio_q, self.started),
                        MacSystemSource("Them", self.audio_q, self.started),
                    ]
                else:
                    self.sources = [
                        WindowsSource("Andrew", self.audio_q, self.started, loopback=False),
                        WindowsSource("Them", self.audio_q, self.started, loopback=True),
                    ]
                for s in self.sources:
                    s.start()
            self.events({"type": "status", "state": "listening", "model": tr.model_name})
            if sys.platform == "darwin" and not self.test_wav:
                threading.Thread(target=self._check_mac_permission, daemon=True).start()
            threading.Thread(target=self._suggest_loop, daemon=True).start()
            while self.running:
                try:
                    chunk = self.audio_q.get(timeout=0.5)
                    lag = time.time() - self.started - chunk.t
                    if lag > MAX_LAG_S and not self.test_wav:
                        skipped = self._skip_to_now()
                        self.events({"type": "status", "state": "skipped", "seconds": round(lag + skipped)})
                        continue
                    tr.feed(chunk)
                except queue.Empty:
                    if self.test_wav and not any(s.is_alive() for s in self.sources):
                        tr.flush_all()
                        self._suggest_now(force=True)
                        self.stop()
                        return
            # Switched off: drop any half-finished sentence rather than transcribe it.
        except Exception as e:  # surface, don't crash the window
            self.events({"type": "error", "message": f"{type(e).__name__}: {e}"})
            self.running = False

    def _skip_to_now(self) -> float:
        """Drop queued audio so the panel stays live instead of building a backlog."""
        dropped = 0.0
        while True:
            try:
                dropped += len(self.audio_q.get_nowait().samples) / 16000
            except queue.Empty:
                return dropped

    def _on_line(self, line: Dict) -> None:
        if not self.running:   # finished transcribing after Andrew switched off: discard
            return
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
        if not self.running:   # Andrew switched off while Claude was thinking: show nothing
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
