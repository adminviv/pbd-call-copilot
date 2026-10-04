"""Live speech-to-text with faster-whisper (free, runs on the PC).

Each speaker's audio is buffered until a pause (or ~12 s of speech), then
transcribed with a science vocabulary prompt so terms like PEX5, CDC48 and
pexophagy come out right instead of "PAX5" or "series 48".
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Dict, List

import numpy as np

from audio import RATE, Chunk

MAX_SEGMENT_S = 12.0
SILENCE_RMS = 0.006      # below this a block counts as silence
SILENCE_TO_FLUSH_S = 0.8

# Words Whisper should expect. Kept short: Whisper only reads ~200 tokens of prompt.
VOCAB = (
    "Peroxisome, peroxisomal, PEX1, PEX2, PEX5, PEX6, PEX10, PEX12, PEX13, PEX14, PEX19, PEX26, "
    "PBD, Zellweger, pexophagy, CDC48, p97, Ubx2, Npl4, Ufd1, Hsp104, ubiquitination, "
    "AAA ATPase, cryo-EM, AlphaFold, plasmalogen, beta-oxidation, PeroxiOS, PBD Project, "
    "Tom Rapoport, Malavika Raman."
)


def pick_model() -> tuple:
    """Largest model that keeps up in real time on this machine."""
    override = os.environ.get("COPILOT_WHISPER_MODEL")
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return override or "large-v3-turbo", "cuda", "float16"
    except Exception:
        pass
    cores = os.cpu_count() or 4
    if override:
        return override, "cpu", "int8"
    return ("small.en" if cores >= 8 else "base.en"), "cpu", "int8"


class LiveTranscriber:
    def __init__(self, on_line: Callable[[Dict], None], extra_vocab: str = ""):
        from faster_whisper import WhisperModel

        name, device, compute = pick_model()
        self.model_name = f"{name} ({device})"
        # Prefer a model shipped next to the app (no download on Andrew's PC).
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))  # packaged app or source
        local = root / "models" / f"faster-whisper-{name}"
        self.model = WhisperModel(str(local) if local.exists() else name, device=device, compute_type=compute)
        self.on_line = on_line
        self.prompt = VOCAB + (" " + extra_vocab if extra_vocab else "")
        self.buffers: Dict[str, List[np.ndarray]] = {}
        self.start_t: Dict[str, float] = {}
        self.silence: Dict[str, float] = {}

    def feed(self, chunk: Chunk) -> None:
        spk = chunk.speaker
        loud = float(np.sqrt(np.mean(chunk.samples ** 2))) if len(chunk.samples) else 0.0
        buf = self.buffers.setdefault(spk, [])
        if loud >= SILENCE_RMS:
            if not buf:
                self.start_t[spk] = chunk.t
            buf.append(chunk.samples)
            self.silence[spk] = 0.0
        elif buf:
            buf.append(chunk.samples)
            self.silence[spk] = self.silence.get(spk, 0.0) + len(chunk.samples) / RATE
        length = sum(len(b) for b in buf) / RATE
        if buf and (self.silence.get(spk, 0) >= SILENCE_TO_FLUSH_S or length >= MAX_SEGMENT_S):
            self._flush(spk)

    def flush_all(self) -> None:
        for spk in list(self.buffers):
            if self.buffers[spk]:
                self._flush(spk)

    def _flush(self, spk: str) -> None:
        audio = np.concatenate(self.buffers[spk])
        self.buffers[spk] = []
        self.silence[spk] = 0.0
        segments, _ = self.model.transcribe(
            audio, language="en", initial_prompt=self.prompt, beam_size=1,
            vad_filter=True, condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        if text:
            self.on_line({"speaker": spk, "t": self.start_t.get(spk, 0.0), "text": text})
