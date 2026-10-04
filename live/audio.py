"""Call audio in, 16 kHz mono chunks out.

Two sources, kept separate so the transcript knows who spoke:
  - "Andrew": the microphone
  - "Them":   everything the PC is playing (the other people on the Zoom call),
              captured with Windows' built-in loopback - nothing joins the call.

FileSource replays a .wav as if it were live, for testing on any machine.
"""
from __future__ import annotations

import queue
import threading
import time
import wave
from dataclasses import dataclass

import numpy as np

RATE = 16000          # what Whisper expects
BLOCK_SECONDS = 0.5   # how often each source hands over audio


@dataclass
class Chunk:
    speaker: str
    samples: np.ndarray  # float32 mono at RATE
    t: float             # seconds since the session started


class _Source(threading.Thread):
    def __init__(self, speaker: str, out: "queue.Queue[Chunk]", t0: float):
        super().__init__(daemon=True)
        self.speaker, self.out, self.t0 = speaker, out, t0
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()


class WindowsSource(_Source):
    """Microphone or speaker loopback through the `soundcard` package (WASAPI)."""

    def __init__(self, speaker: str, out, t0: float, loopback: bool):
        super().__init__(speaker, out, t0)
        self.loopback = loopback

    def run(self) -> None:
        import soundcard as sc  # Windows/WASAPI; imported here so tests run anywhere

        if self.loopback:
            device = sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True)
        else:
            device = sc.default_microphone()
        frames = int(RATE * BLOCK_SECONDS)
        with device.recorder(samplerate=RATE, channels=1, blocksize=frames) as rec:
            while not self.stop_event.is_set():
                data = rec.record(numframes=frames)
                mono = data.mean(axis=1) if data.ndim > 1 else data
                self.out.put(Chunk(self.speaker, mono.astype(np.float32), time.time() - self.t0))


class FileSource(_Source):
    """Plays a 16-bit mono/stereo .wav into the pipeline at real speed (speed=1)."""

    def __init__(self, speaker: str, out, t0: float, path: str, speed: float = 1.0):
        super().__init__(speaker, out, t0)
        self.path, self.speed = path, speed

    def run(self) -> None:
        with wave.open(self.path) as w:
            rate, channels = w.getframerate(), w.getnchannels()
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)
        if rate != RATE:
            idx = np.arange(0, len(audio), rate / RATE)
            audio = np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)
        step = int(RATE * BLOCK_SECONDS)
        for i in range(0, len(audio), step):
            if self.stop_event.is_set():
                return
            self.out.put(Chunk(self.speaker, audio[i:i + step], time.time() - self.t0))
            time.sleep(BLOCK_SECONDS / self.speed)
