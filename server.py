"""English Tutor v1 — FastAPI backend (loopback only).

Endpoints:
  GET  /                static tutor page
  GET  /api/health      component status
  POST /api/transcribe  audio upload -> {text, elapsed_s}   (faster-whisper, CPU int8)
  POST /api/chat        {text, mode, history, playbook} -> {reply, corrections, rephrase}
  POST /api/tts         {text} -> mp3 bytes   (edge-tts)
  GET  /api/digest      weekly recurring-error digest (SQL + LLM advice)
  GET  /api/playbooks   playbook scene cards for roleplay
  GET  /api/review      warm-up queue (corrections aged >= 2 days, not retired)
  POST /api/review      {correction_id, recall: hit|miss}
  GET  /api/drill/phrases    voice-drill phrase set (playbook key phrases or recent fixes)
  POST /api/drill/score     {expected, heard} -> text-similarity verdict (NOT phonemes)

Config: .env next to this file (see .env.example). Modules read env at call time,
so .env is loaded here before the submodules are imported.
"""
import difflib
import os
import re
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
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
import playbooks  # noqa: E402
import tracker  # noqa: E402
import tutor  # noqa: E402
import tts  # noqa: E402

app = FastAPI(title="English Tutor", version="0.1.0")

# PWA shell: icons + brand.svg under /static; manifest at root scope with the
# right MIME (StaticFiles would guess octet-stream for .webmanifest).
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/manifest.webmanifest")
def webmanifest():
    return FileResponse(
        BASE / "static" / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


class ChatIn(BaseModel):
    text: str
    mode: str = "free"  # free | rephrase | drill | roleplay
    history: list[dict] = []
    playbook: str = ""  # scene-card id, required when mode == "roleplay"


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
        "tts": tts.status(),
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
    if body.mode not in ("free", "rephrase", "drill", "roleplay"):
        raise HTTPException(400, f"unknown mode: {body.mode}")
    card = None
    if body.mode == "roleplay":
        card = playbooks.load_card(body.playbook)
        if card is None:
            raise HTTPException(404, f"unknown playbook: {body.playbook!r}")
    try:
        result, elapsed = tutor.reply(body.text, body.mode, body.history, playbook_card=card)
    except Exception as exc:
        raise HTTPException(500, f"tutor failed: {exc}") from exc
    # v3: persist turn + corrections (log_turn never raises — DB failure can't break tutoring)
    tracker.log_turn(body.mode, body.text, result["reply"], result["corrections"])
    return {
        "reply": result["reply"],
        "corrections": result["corrections"],
        "rephrase": result["rephrase"],
        "elapsed_s": round(elapsed, 2),
    }


@app.get("/api/digest")
def digest(days: int = 7):
    """Weekly recurring-error digest: deterministic SQL tag counts + LLM advice/focus."""
    days = max(1, min(days, 31))
    try:
        return tracker.build_digest(days)
    except Exception as exc:
        raise HTTPException(500, f"digest failed: {exc}") from exc


@app.get("/api/playbooks")
def list_playbooks():
    """Playbook scene cards for the roleplay mode."""
    return {"playbooks": playbooks.list_playbooks()}


class ReviewIn(BaseModel):
    correction_id: int
    recall: str  # hit | miss


@app.get("/api/review")
def review_queue(limit: int = 3):
    """Warm-up queue: corrections aged >= 2 days, not retired by 2 consecutive hits."""
    limit = max(1, min(limit, 10))
    return {"items": tracker.review_queue(limit)}


@app.post("/api/review")
def record_review(body: ReviewIn):
    if not tracker.record_review(body.correction_id, body.recall):
        raise HTTPException(400, "invalid correction_id or recall value")
    return {"ok": True}


class ScoreIn(BaseModel):
    expected: str
    heard: str


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", s.lower()).strip()


@app.post("/api/drill/score")
def drill_score(body: ScoreIn):
    """Text-similarity proxy for the voice drill — catches omissions/substitutions, NOT phonemes."""
    if not body.expected.strip() or not body.heard.strip():
        raise HTTPException(400, "expected and heard are required")
    ratio = difflib.SequenceMatcher(None, _normalize(body.expected), _normalize(body.heard)).ratio()
    verdict = "hit" if ratio >= 0.85 else ("close" if ratio >= 0.70 else "miss")
    return {"ratio": round(ratio, 3), "verdict": verdict}


@app.get("/api/drill/phrases")
def drill_phrases(playbook: str = ""):
    """Phrase set for the voice drill: playbook key phrases, else recent corrections."""
    fallback = tracker.recent_corrected(days=7, limit=5)
    phrases, source = playbooks.drill_phrases(playbook or None, fallback)
    return {"phrases": phrases, "source": source}


@app.post("/api/tts")
async def tts_endpoint(body: TtsIn):
    if not body.text.strip():
        raise HTTPException(400, "empty text")
    try:
        audio = await tts.synthesize(body.text)
    except Exception as exc:
        raise HTTPException(502, f"tts failed: {exc}") from exc
    return Response(audio, media_type=tts.LAST_MEDIA_TYPE)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("TUTOR_PORT", "8770")))
