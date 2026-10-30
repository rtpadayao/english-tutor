"""English Tutor v1 — FastAPI backend (loopback only).

Endpoints:
  GET  /                static tutor page
  GET  /api/health      component status
  POST /api/transcribe  audio upload -> {text, elapsed_s}   (faster-whisper, CPU int8)
  POST /api/chat        {text, mode, history} -> {reply, elapsed_s}  (Ollama)
  POST /api/tts         {text} -> mp3 bytes   (edge-tts)

Config: .env next to this file (see .env.example). Modules read env at call time,
so .env is loaded here before the submodules are imported.
"""
import os
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)


def _load_env() -> None:
    env_file = BASE / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())


_load_env()

import asr  # noqa: E402  (import after .env load)
import tutor  # noqa: E402
import tts  # noqa: E402

app = FastAPI(title="English Tutor", version="0.1.0")


class ChatIn(BaseModel):
    text: str
    mode: str = "free"  # free | rephrase | drill
    history: list[dict] = []


class TtsIn(BaseModel):
    text: str


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/health")
def health():
    return {
        "ollama": tutor.ollama_ok(),
        "whisper": asr.status(),
        "tts": "edge-tts",
        "model": os.environ.get("OLLAMA_MODEL", "qwen3.5:9b"),
    }


@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...)):
    suffix = Path(file.filename or "audio.webm").suffix or ".webm"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = DATA / f"in-{stamp}{suffix}"
    path.write_bytes(await file.read())
    try:
        text, elapsed = asr.transcribe(path)
    except Exception as exc:  # surface real errors to the page
        raise HTTPException(500, f"transcription failed: {exc}") from exc
    return {"text": text, "elapsed_s": round(elapsed, 2)}


@app.post("/api/chat")
def chat(body: ChatIn):
    if not body.text.strip():
        raise HTTPException(400, "empty text")
    if body.mode not in ("free", "rephrase", "drill"):
        raise HTTPException(400, f"unknown mode: {body.mode}")
    try:
        result, elapsed = tutor.reply(body.text, body.mode, body.history)
    except Exception as exc:
        raise HTTPException(500, f"tutor failed: {exc}") from exc
    return {
        "reply": result["reply"],
        "corrections": result["corrections"],
        "rephrase": result["rephrase"],
        "elapsed_s": round(elapsed, 2),
    }


@app.post("/api/tts")
async def tts_endpoint(body: TtsIn):
    if not body.text.strip():
        raise HTTPException(400, "empty text")
    try:
        audio = await tts.synthesize(body.text)
    except Exception as exc:
        raise HTTPException(502, f"tts failed: {exc}") from exc
    return Response(audio, media_type="audio/mpeg")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("TUTOR_PORT", "8770")))
