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
import knowledge  # noqa: E402
import recordings  # noqa: E402
import numpy as np  # noqa: E402
from audio import FileSource, MacMicSource, MacSystemSource, WindowsSource  # noqa: E402
from transcriber import LiveTranscriber  # noqa: E402

SUGGEST_EVERY_S = 75      # how often a new stretch of conversation is analysed
MIN_NEW_WORDS = 60        # skip if little was said since last time
MIN_THEM_WORDS = 60       # no cards until the other side has actually said something (not just hellos)
MAX_CARDS = 2             # Andrew: "2 sounds good"
MAX_LAG_S = 20            # if speech-to-text falls this far behind, skip ahead to the present
SLOW_LAG_S = 5            # this far behind: switch to the faster speech model (once)
SILENT_WARN_S = 45        # warn if one side of the call has been silent this long
HEARD_RMS = 0.006         # same threshold the transcriber uses for "someone is speaking"
TRANSCRIPTS = Path.home() / "Documents" / "PBD Call Copilot" / "transcripts"


class CallSession:
    def __init__(self, events: Callable[[Dict], None], test_wav: Optional[str] = None,
                 offline: bool = False, meeting: str = "", meeting_info: Optional[Dict] = None):
        self.events = events
        self.meeting = meeting   # calendar details of the confirmed meeting (title, people, agenda)
        self.meeting_info = meeting_info or {}
        self.cards_shown: List[Dict] = []   # everything kept for Recordings
        self.plain: List[Dict] = []
        self.starred: List[str] = []
        self.test_wav = test_wav
        self.offline = offline   # self-test without Claude: audio + speech-to-text only
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
        try:
            self._save()
        except Exception as e:   # a full disk must never stop the switch from turning off
            self.events({"type": "warning", "message": f"Couldn't save this call: {type(e).__name__}"})
        self.events({"type": "status", "state": "off"})

    def star(self, ask: str) -> None:
        if ask in self.starred:
            self.starred.remove(ask)
        else:
            self.starred.append(ask)

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
            problem = "" if self.offline else self._self_check()
            if problem:   # say exactly what's wrong instead of "Listening" to nothing
                self.events({"type": "error", "message": problem})
                self.stop()
                return
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
            if not self.offline:
                threading.Thread(target=self._suggest_loop, daemon=True).start()
                threading.Thread(target=self._check_papers, daemon=True).start()
            self.last_heard = {s.speaker: time.time() for s in self.sources}
            warned = ""
            while self.running:
                try:
                    chunk = self.audio_q.get(timeout=0.5)
                    lag = time.time() - self.started - chunk.t
                    if lag > SLOW_LAG_S and tr.use_faster_model():
                        self.events({"type": "status", "state": "listening", "model": tr.model_name})
                    if lag > MAX_LAG_S and not self.test_wav:
                        skipped = self._skip_to_now()
                        self.events({"type": "warning", "message":
                                     f"This computer is transcribing slowly, so {round(lag + skipped)} s of audio "
                                     "was skipped to stay live."})
                        continue
                    rms = float(np.sqrt(np.mean(chunk.samples ** 2))) if len(chunk.samples) else 0.0
                    self.events({"type": "level", "speaker": chunk.speaker, "level": min(1.0, (rms / 0.12) ** 0.5)})
                    if rms >= HEARD_RMS:
                        self.last_heard[chunk.speaker] = time.time()
                    warned = self._check_hearing(warned)
                    tr.feed(chunk)
                except queue.Empty:
                    if self.test_wav and not any(s.is_alive() for s in self.sources):
                        tr.flush_all()
                        if not self.offline:
                            self._suggest_now(force=True)
                        self.stop()
                        return
            # Switched off: drop any half-finished sentence rather than transcribe it.
        except Exception as e:  # surface, don't crash the window
            self.events({"type": "error", "message": f"{type(e).__name__}: {e}"})
            self.running = False

    def _self_check(self) -> str:
        """Before listening: can we reach Claude with this key? Returns a problem or ''."""
        try:
            copilot.get_client().models.list(limit=1)
            return ""
        except Exception as e:
            name = type(e).__name__
            if name in ("AuthenticationError", "PermissionDeniedError"):
                return ("The PBD key on this computer was not accepted, so no suggestions can be made. "
                        "Ask Genevieve for the current key.")
            if name in ("APIConnectionError", "APITimeoutError"):
                return "No internet connection. Suggestions need internet; check Wi-Fi and switch on again."
            return f"Can't reach Claude right now ({name}). Try switching on again in a minute."

    def _check_papers(self) -> None:
        """Warn (don't stop) if no paper source works: cards would have no paper links."""
        if knowledge.PEROXIOS_DB.exists() or knowledge.search_europepmc(["peroxisome"], limit=1):
            return
        self.events({"type": "warning", "message":
                     "Can't reach the paper search, so suggestions may come without paper links."})

    def _check_hearing(self, warned: str) -> str:
        """Tell Andrew if one side of the call is silent, so 'Listening' is never misleading."""
        if self.test_wav:
            return warned
        now = time.time()
        quiet = [spk for spk, t in self.last_heard.items() if now - t > SILENT_WARN_S]
        if quiet == ["Them"]:
            msg = ("Not hearing the other side of the call. Is the call's sound playing on this computer "
                   "(not on your phone)?")
        elif quiet == ["Andrew"]:
            msg = "Not hearing your microphone. Check the microphone in your computer's sound settings."
        elif quiet:
            msg = "Not hearing anything yet. Is the call running on this computer?"
        else:
            msg = ""
        if msg != warned:
            self.events({"type": "warning", "message": msg} if msg else {"type": "ok"})
        return msg

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
        them = sum(len(l["text"].split()) for l in self.lines if l["speaker"] != "Andrew")
        if not force and (words < MIN_NEW_WORDS or them < MIN_THEM_WORDS):
            return
        self.last_suggest_at = time.time()
        if not new:
            return
        self.processed += len(new)
        try:
            res = copilot.suggest(_render(new), _render(history), self.shown_asks, meeting=self.meeting)
        except Exception as e:
            self.events({"type": "error", "message": f"Suggestion step failed: {e}"})
            return
        if not self.running:   # Andrew switched off while Claude was thinking: show nothing
            return
        cards = res.get("cards", [])[:MAX_CARDS]
        self.shown_asks += [c["ask"] for c in cards]
        at = _clock(new[-1]["t"])
        self.cards_shown += [{**c, "at": at} for c in cards]
        if res.get("topic") or res.get("in_plain_words"):
            self.plain.append({"at": at, "topic": res.get("topic") or "", "text": res.get("in_plain_words", "")})
        self.events({"type": "suggestions", "topic": res.get("topic"),
                     "in_plain_words": res.get("in_plain_words", ""), "cards": cards,
                     "at": _clock(new[-1]["t"])})

    def _save(self) -> None:
        if not self.lines:
            return
        TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
        path = TRANSCRIPTS / f"{datetime.now():%Y-%m-%d_%H%M}_call.txt"
        path.write_text(_render(self.lines))
        started = datetime.fromtimestamp(self.started)
        call_id = recordings.save({
            "id": f"{started:%Y%m%d-%H%M%S}", "title": self.meeting_info.get("title") or "Call",
            "attendees": self.meeting_info.get("attendees", []), "started": started.isoformat(timespec="seconds"),
            "minutes": max(1, round((time.time() - self.started) / 60)),
            "topic": self.plain[-1]["topic"] if self.plain else "",
            "lines": self.lines, "plain": self.plain, "cards": self.cards_shown, "starred": self.starred,
        })
        self.events({"type": "status", "state": "saved", "path": str(path), "recording": call_id})


def _clock(t: float) -> str:
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


def _render(lines: List[Dict]) -> str:
    return "\n".join(f"[{_clock(l['t'])}] {l['speaker']}: {l['text']}" for l in lines)
