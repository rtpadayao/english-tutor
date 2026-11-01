#!/usr/bin/env bash
# setup_piper.sh - one-time setup for the local (offline) tutor voice.
# Installs piper-tts into the tutor venv and downloads the voice into
# data/piper/ (gitignored). Runtime needs NO network; this script needs it
# once, only to download the voice files.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$HOME/venvs/english-tutor/bin/python"
VOICE_DIR="$ROOT/data/piper"
VOICE="en_US-lessac-medium"
BASE_URL="${PIPER_VOICE_BASE_URL:-https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium}"

if [ ! -x "$PY" ]; then
  echo "ERROR: venv python not found at $PY (see scripts/start.sh prereqs)"
  exit 1
fi

"$PY" -c "import piper" 2>/dev/null || "$PY" -m pip install --quiet piper-tts

mkdir -p "$VOICE_DIR"
for ext in onnx onnx.json; do
  f="$VOICE_DIR/$VOICE.$ext"
  if [ ! -f "$f" ]; then
    echo "downloading $(basename "$f") ..."
    curl -L --fail --silent --show-error -o "$f" "$BASE_URL/$VOICE.$ext"
  fi
done

ls -la "$VOICE_DIR"
echo
echo "Sanity check (writes /tmp/piper-setup-test.wav):"
echo "Hello from Piper, running fully offline." | "$PY" -m piper \
  --model "$VOICE_DIR/$VOICE.onnx" --output_file /tmp/piper-setup-test.wav
ls -la /tmp/piper-setup-test.wav
echo "OK - TTS_PROVIDER=piper (default) will now work offline."
