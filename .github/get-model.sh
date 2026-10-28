#!/usr/bin/env bash
# Downloads the faster-whisper model that ships inside the app.
set -euo pipefail
# base.en is the default; tiny.en is the automatic fallback on slow computers.
for MODEL in ${WHISPER_MODELS:-base.en tiny.en}; do
  mkdir -p "models/faster-whisper-$MODEL"
  for f in config.json tokenizer.json vocabulary.txt model.bin; do
    curl -sfL --retry 5 "https://huggingface.co/Systran/faster-whisper-$MODEL/resolve/main/$f" \
      -o "models/faster-whisper-$MODEL/$f"
  done
done

# Parakeet (main model since v1 build 19): NVIDIA's English speech model, int8, run with sherpa-onnx.
PARAKEET=sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8
if [ ! -f "models/$PARAKEET/encoder.int8.onnx" ]; then
  curl -sfL --retry 5 "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$PARAKEET.tar.bz2" -o parakeet.tar.bz2
  mkdir -p models && tar xjf parakeet.tar.bz2 -C models && rm parakeet.tar.bz2
  rm -rf "models/$PARAKEET/test_wavs"
fi
