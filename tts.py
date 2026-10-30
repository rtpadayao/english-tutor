"""edge-tts wrapper.

Free Microsoft neural voices, CPU-only, needs internet. Fallback plan (Piper, offline)
is documented in the Obsidian plan note — not built in v1. Voice is read at call time.
"""
import io
import os


async def synthesize(text: str, voice: str | None = None) -> bytes:
    """Return mp3 bytes for the given text."""
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
    return audio
