"""Live speech-to-text on the user's own computer (free, nothing leaves the PC).

Each speaker's audio is buffered until a pause (or 8 s of speech), then transcribed.

Main model: NVIDIA Parakeet (via sherpa-onnx). On 19 minutes of test calls it got
~90% of words right vs ~54% for Whisper base, and ran faster (8x real time on a
1.2 GHz Intel laptop). It spells unknown science words by sound ("paroxysm"), so
corrections.py maps those back to the PBD term.

Backup: Whisper (faster-whisper) base.en, then tiny.en on slow computers. Used if
Parakeet can't load, so the app always transcribes.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Callable, Dict, List

import numpy as np

import corrections
from audio import RATE, Chunk

log = logging.getLogger("copilot")

MAX_SEGMENT_S = 8.0     # shorter pieces = text on screen sooner (was 12 s)
SILENCE_RMS = 0.006      # below this a block counts as silence
SILENCE_TO_FLUSH_S = 0.8
THREADS = 4
PARAKEET = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"

# Words Whisper should expect (backup model only). Kept short: Whisper only reads ~200 tokens of prompt.
VOCAB = (
    "Peroxisome, peroxisomal, PEX1, PEX2, PEX5, PEX6, PEX10, PEX12, PEX13, PEX14, PEX19, PEX26, "
    "PBD, Zellweger, pexophagy, CDC48, p97, Ubx2, Npl4, Ufd1, Hsp104, ubiquitination, "
    "AAA ATPase, cryo-EM, AlphaFold, plasmalogen, beta-oxidation, PeroxiOS, PBD Project, "
    "Tom Rapoport, Malavika Raman."
)


def pick_model() -> str:
    """'parakeet' unless told otherwise (COPILOT_STT=whisper) or its files are missing."""
    return os.environ.get("COPILOT_STT", "parakeet")


class LiveTranscriber:
    def __init__(self, on_line: Callable[[Dict], None], extra_vocab: str = ""):
        # Prefer models shipped next to the app (no download on the user's PC).
        self.root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))  # packaged app or source
        self.on_line = on_line
        self.prompt = VOCAB + (" " + extra_vocab if extra_vocab else "")
        self.buffers: Dict[str, List[np.ndarray]] = {}
        self.start_t: Dict[str, float] = {}
        self.silence: Dict[str, float] = {}
        self._run = None
        if pick_model() == "parakeet":
            try:
                self._load_parakeet()
            except Exception:
                log.exception("Parakeet speech model could not load; using Whisper instead")
        if self._run is None:
            self._load_whisper(os.environ.get("COPILOT_WHISPER_MODEL", "base.en"))

    # -- models ------------------------------------------------------------
    def _load_parakeet(self) -> None:
        import sherpa_onnx
        d = self.root / "models" / PARAKEET
        rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(d / "encoder.int8.onnx"), decoder=str(d / "decoder.int8.onnx"),
            joiner=str(d / "joiner.int8.onnx"), tokens=str(d / "tokens.txt"),
            model_type="nemo_transducer", num_threads=THREADS)

        def run(audio: np.ndarray) -> str:
            s = rec.create_stream()
            s.accept_waveform(RATE, audio)
            rec.decode_stream(s)
            return s.result.text
        self._run = run
        self.model_name = "Parakeet (on this computer)"

    def _load_whisper(self, name: str) -> None:
        from faster_whisper import WhisperModel
        local = self.root / "models" / f"faster-whisper-{name}"
        model = WhisperModel(str(local) if local.exists() else name, device="cpu", compute_type="int8",
                             cpu_threads=THREADS)

        def run(audio: np.ndarray) -> str:
            segments, _ = model.transcribe(
                audio, language="en", initial_prompt=self.prompt, beam_size=1,
                vad_filter=True, condition_on_previous_text=False,
                # Never let one piece stall the call: no retry passes, and a hard cap on
                # output length (8 s of speech is ~40 tokens). Without this, a piece the
                # model loops on took 37 s instead of under 1 s.
                temperature=0.0, max_new_tokens=96, without_timestamps=True,
            )
            return " ".join(s.text.strip() for s in segments)
        self._run = run
        self.model_name = f"Whisper {name} (on this computer)"

    def use_faster_model(self) -> bool:
        """Slow computer: swap to the smallest model shipped with the app (Whisper tiny,
        less accurate but light). Returns True if it switched."""
        if self.model_name.startswith("Whisper tiny") or not (self.root / "models" / "faster-whisper-tiny.en").exists():
            return False
        log.info("falling behind: switching to Whisper tiny")
        self._load_whisper("tiny.en")
        self.model_name = "Whisper tiny (faster for this computer)"
        return True

    # -- audio in, lines out -----------------------------------------------
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
        audio = np.concatenate(self.buffers[spk]).astype(np.float32)
        self.buffers[spk] = []
        self.silence[spk] = 0.0
        text = corrections.fix(self._run(audio).strip())
        if text:
            self.on_line({"speaker": spk, "t": self.start_t.get(spk, 0.0), "text": text})
