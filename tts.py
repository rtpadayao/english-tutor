"""TTS for the tutor: Piper (local, offline) is the default; edge-tts is the fallback.

Piper    - on-device neural voice (piper-tts, ONNX, CPU). No network, no keys.
           Voice files live in data/piper/ (gitignored) - run scripts/setup_piper.sh once.
edge-tts - Microsoft cloud neural voices; needs internet. Used (a) as automatic
           fallback when Piper fails (voice missing, synth error), and
           (b) directly when TTS_PROVIDER=edge is set.

Env (read at call time, like asr.py):
  TTS_PROVIDER        piper (default) | edge
  PIPER_VOICE         path to the .onnx voice (default data/piper/en_US-lessac-medium.onnx)
  PIPER_LENGTH_SCALE  1.0-2.0; higher = slower speech (default 1.0)
  EDGE_TTS_VOICE      edge-tts voice name (default en-US-JennyNeural)

synthesize() returns raw audio bytes and sets tts.LAST_MEDIA_TYPE so the
endpoint serves the right Content-Type:
  piper -> audio/wav (RIFF PCM 22050 Hz mono 16-bit)
  edge  -> audio/mpeg
"""
import io
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger("tts")

BASE = Path(__file__).resolve().parent
LAST_MEDIA_TYPE = "audio/wav"

_MIN_AUDIO_BYTES = 1000  # shorter than this is a broken synthesis, not speech


def _provider() -> str:
    return os.environ.get("TTS_PROVIDER", "piper").strip().lower() or "piper"


def _piper_voice() -> Path:
    env = os.environ.get("PIPER_VOICE")
    return Path(env).expanduser() if env else BASE / "data" / "piper" / "en_US-lessac-medium.onnx"


def status() -> str:
    """One-line TTS status for /api/health."""
    provider = _provider()
    if provider == "edge":
        return "edge-tts (cloud, forced)"
    voice = _piper_voice()
    if voice.exists():
        return f"piper ({voice.stem}, local)"
    return f"piper (voice missing: {voice.name} - will fall back to edge-tts)"


async def _piper_synthesize(text: str) -> bytes:
    """Local piper CLI (same venv python, local ONNX voice) -> WAV bytes. No network."""
    global LAST_MEDIA_TYPE
    voice = _piper_voice()
    if not voice.exists():
        raise RuntimeError(f"piper voice missing: {voice}")
    config = voice.with_suffix(".onnx.json")  # sibling json; piper locates it itself
    if not config.exists():
        raise RuntimeError(f"piper voice config missing: {config}")
    fd, out_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        cmd = [
            sys.executable, "-m", "piper",
            "--model", str(voice),
            "--length-scale", os.environ.get("PIPER_LENGTH_SCALE", "1.0"),
            "--output_file", out_path,
        ]
        proc = subprocess.run(
            cmd, input=text.encode("utf-8"), capture_output=True, timeout=120
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"piper exited {proc.returncode}: {proc.stderr.decode('utf-8', 'replace')[:300]}"
            )
        audio = Path(out_path).read_bytes()
    finally:
        Path(out_path).unlink(missing_ok=True)
    if len(audio) < _MIN_AUDIO_BYTES:
        raise RuntimeError(f"piper produced suspiciously small audio ({len(audio)} bytes)")
    LAST_MEDIA_TYPE = "audio/wav"
    return audio


async def _edge_synthesize(text: str, voice: str | None = None) -> bytes:
    """Microsoft cloud voices (edge-tts). Needs internet."""
    global LAST_MEDIA_TYPE
    import edge_tts

    chosen = voice or os.environ.get("EDGE_TTS_VOICE", "en-US-JennyNeural")
    buffer = io.BytesIO()
    communicate = edge_tts.Communicate(text, chosen, rate="-4%")
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.write(chunk["data"])
    audio = buffer.getvalue()
    if len(audio) < 500:
        raise RuntimeError(f"edge-tts produced suspiciously small audio ({len(audio)} bytes)")
    LAST_MEDIA_TYPE = "audio/mpeg"
    return audio


async def synthesize(text: str, voice: str | None = None) -> bytes:
    """Return audio bytes for text. `voice` is an edge-tts voice name (ignored by piper)."""
    provider = _provider()
    if provider == "edge":
        return await _edge_synthesize(text, voice)
    try:
        return await _piper_synthesize(text)
    except Exception as exc:
        log.warning("piper failed (%s) - falling back to edge-tts (needs internet)", exc)
        return await _edge_synthesize(text, voice)
