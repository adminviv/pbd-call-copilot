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
