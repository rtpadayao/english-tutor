# english-tutor

Local, voice-first English tutor for professional / client English practice.
Thin v1 built from scratch (design + repo audit: see Obsidian note
`2026-10-30_english-ai-tutor_plan.md` in the vault).

Stack: faster-whisper (small, int8, CPU) + Ollama qwen3.5:9b + edge-tts + FastAPI + one static page.
Design rule: ASR and TTS run on CPU; the 8 GB VRAM is reserved for the LLM.

## Run

    python3 -m venv ~/venvs/english-tutor
    ~/venvs/english-tutor/bin/pip install -r requirements.txt
    cp .env.example .env
    ~/venvs/english-tutor/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8770
    # open http://127.0.0.1:8770

Prereqs: Ollama running on :11434 with `qwen3.5:9b` pulled; internet for edge-tts.

## Endpoints

    GET  /                tutor page
    GET  /api/health      component status
    POST /api/transcribe  multipart file=<audio> -> {text, elapsed_s}
    POST /api/chat        {text, mode, history}   -> {reply, elapsed_s}
    POST /api/tts         {text}                  -> audio/mpeg

## Smoke test (server must be running)

    ~/venvs/english-tutor/bin/python scripts/smoke_test.py

File-driven loop: tts -> transcribe -> chat -> tts, with latency report.

## Modes

- free      conversation; tutor answers first, then max 3 gentle corrections
- rephrase  "how do I say X" -> professional phrasing + variants
- drill     strict corrections (full JSON feedback panel arrives in v2)

## License

Personal project — no license file yet. Add one before ever sharing the repo
(the no-license finding on the audited reference repos is the cautionary tale).
