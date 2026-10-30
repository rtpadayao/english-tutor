"""faster-whisper ASR wrapper.

Lazy-loaded single model, CPU int8 — VRAM is reserved for the LLM (see plan, Section 4/5).
Env is read at call time so .env loading order never matters.
"""
import os
import threading
import time

_model = None
_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from faster_whisper import WhisperModel

                _model = WhisperModel(
                    os.environ.get("WHISPER_MODEL", "small"),
                    device=os.environ.get("WHISPER_DEVICE", "cpu"),
                    compute_type=os.environ.get("WHISPER_COMPUTE", "int8"),
                )
    return _model


def transcribe(path) -> tuple[str, float]:
    """Return (text, elapsed_seconds) for an audio file (webm/opus, mp3, wav...)."""
    start = time.perf_counter()
    segments, _info = _get_model().transcribe(str(path), language="en", vad_filter=True)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return text, time.perf_counter() - start


def status() -> str:
    return "loaded" if _model is not None else "lazy (loads on first use)"
